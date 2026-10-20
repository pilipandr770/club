import json

from app.extensions import db
from app.models import Match, Profile, User
from app.services import matching
from app.services.llm import LLMUnavailable
from app.services.matching import (
    LexicalEmbedder,
    cosine,
    fit_label,
    get_matches,
    profile_version,
    refresh_embeddings,
    score,
    top_matches,
)
from app.utils import utcnow
from tests.conftest import login, make_user


def _member(app, email, plan="growth", allow=True, verified=True, status="active", **fields):
    user_id = make_user(app, email=email, verified=verified, plan=plan, status=status)
    with app.app_context():
        user = db.session.get(User, user_id)
        profile = user.profile
        base = dict(headline="Consultant", q_focus="I advise founders on tax and bookkeeping.",
                    q_can_help="I explain tax deadlines for freelancers.",
                    q_looking_for="A designer for a two-week project.",
                    q_challenge="Finding clients outside my network.",
                    allow_matching=allow, visible_in_directory=True)
        base.update(fields)
        for key, value in base.items():
            setattr(profile, key, value)
        refresh_embeddings(profile)
        db.session.commit()
    return user_id


# --- эмбеддинги и версии -------------------------------------------------------

def test_lexical_embedder_is_deterministic_and_related_texts_are_closer():
    embedder = LexicalEmbedder()
    a, b, c = embedder.embed(["tax advice for freelancers", "tax advice for founders",
                              "gardening and roses in spring"])

    assert embedder.embed(["tax advice for freelancers"])[0] == a
    assert cosine(a, b) > cosine(a, c)


def test_empty_text_gives_zero_vector_not_crash():
    assert LexicalEmbedder().embed([""])[0] == [0.0] * matching.DIM
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_profile_version_changes_only_with_matching_text(app):
    user_id = make_user(app, verified=True)
    with app.app_context():
        profile = db.session.get(User, user_id).profile
        before = profile_version(profile)
        profile.city = "Hamburg"  # город не участвует в матчинге
        assert profile_version(profile) == before
        profile.q_focus = "Something else entirely."
        assert profile_version(profile) != before


def test_refresh_recomputes_only_when_text_changes(app):
    user_id = make_user(app, verified=True)
    with app.app_context():
        profile = db.session.get(User, user_id).profile
        profile.q_focus = "Tax"
        assert refresh_embeddings(profile) is True
        assert refresh_embeddings(profile) is False
        profile.q_focus = "Tax and payroll"
        assert refresh_embeddings(profile) is True


# --- формула и метки ------------------------------------------------------------

def test_score_uses_the_agreed_weights():
    a = Profile(embed_need=[1.0, 0.0], embed_offer=[0.0, 1.0])
    b = Profile(embed_need=[0.0, 1.0], embed_offer=[1.0, 0.0])

    # need_a·offer_b = 1, need_b·offer_a = 1 → 0.6 + 0.4
    assert abs(score(a, b) - 1.0) < 1e-9

    c = Profile(embed_need=[1.0, 0.0], embed_offer=[0.0, 0.0])
    # только первое слагаемое: need_a·offer_c = 0, need_c·offer_a = 0 → 0
    assert score(a, c) == 0.0


def test_fit_label_thresholds():
    assert fit_label(0.5) == "gut passend"
    assert fit_label(0.3) == "teilweise passend"
    assert fit_label(0.01) == "wenig Überschneidung"


# --- подбор -------------------------------------------------------------------

def test_candidates_exclude_self_unmatchable_unverified_and_blocked(app):
    me = _member(app, "me@example.com")
    good = _member(app, "good@example.com")
    _member(app, "optout@example.com", allow=False)
    _member(app, "unverified@example.com", verified=False)
    _member(app, "blocked@example.com", status="blocked")
    with app.app_context():
        me_user = db.session.get(User, me)
        ids = [other.id for other, _ in top_matches(me_user)]

    assert ids == [good]


def test_nobody_gets_matches_without_own_consent(app):
    me = _member(app, "off@example.com", allow=False)
    _member(app, "other@example.com")
    with app.app_context():
        assert top_matches(db.session.get(User, me)) == []


# --- объяснения и кеш ---------------------------------------------------------

def test_template_explanation_is_cached_and_not_recalculated(app):
    me = _member(app, "me@example.com")
    other = _member(app, "other@example.com")
    app.config["ANTHROPIC_API_KEY"] = ""
    with app.app_context():
        first = get_matches(db.session.get(User, me))
        second = get_matches(db.session.get(User, me))

        assert [o.id for o, _, _ in first] == [other]
        assert first[0][2] == second[0][2]
        match = Match.query.one()
        assert match.explanation_source == "template"
        assert "Thema" in match.explanation


def test_llm_explanations_are_used_and_cached(app, monkeypatch):
    me = _member(app, "me@example.com")
    _member(app, "other@example.com")
    app.config["ANTHROPIC_API_KEY"] = "test-key"
    calls = []

    def fake_ask(system, user, max_tokens=900, timeout=30):
        calls.append(user)
        return json.dumps({"1": "Beide suchen Steuerhilfe für Freiberufler."})

    monkeypatch.setattr(matching, "ask", fake_ask)
    with app.app_context():
        result = get_matches(db.session.get(User, me))
        assert result[0][2] == "Beide suchen Steuerhilfe für Freiberufler."
        assert Match.query.one().explanation_source == "llm"
        get_matches(db.session.get(User, me))

    assert len(calls) == 1  # второй раз ничего не считается
    assert "Name" not in calls[0] and "@" not in calls[0]  # в запрос не уходят имена и почта
    app.config["ANTHROPIC_API_KEY"] = ""


def test_llm_failure_falls_back_to_template(app, monkeypatch):
    me = _member(app, "me@example.com")
    _member(app, "other@example.com")
    app.config["ANTHROPIC_API_KEY"] = "test-key"

    def broken(*args, **kwargs):
        raise LLMUnavailable("timeout")

    monkeypatch.setattr(matching, "ask", broken)
    with app.app_context():
        result = get_matches(db.session.get(User, me))
        assert result and "Thema" in result[0][2]
        assert Match.query.one().explanation_source == "template"
    app.config["ANTHROPIC_API_KEY"] = ""


def test_template_is_replaced_once_a_key_appears(app, monkeypatch):
    me = _member(app, "me@example.com")
    _member(app, "other@example.com")
    app.config["ANTHROPIC_API_KEY"] = ""
    with app.app_context():
        get_matches(db.session.get(User, me))

    app.config["ANTHROPIC_API_KEY"] = "test-key"
    monkeypatch.setattr(matching, "ask", lambda *a, **k: json.dumps({"1": "Gute Passung."}))
    with app.app_context():
        result = get_matches(db.session.get(User, me))
        assert result[0][2] == "Gute Passung."
    app.config["ANTHROPIC_API_KEY"] = ""


# --- страница и приватность ---------------------------------------------------

def test_matches_page_shows_hints_until_profile_is_ready(app, client):
    make_user(app, email="new@example.com", verified=True)
    login(client, "new@example.com")

    body = client.get("/profile/matches").get_data(as_text=True)

    assert "Matching funktioniert, sobald dein Profil sagt, was du anbietest und was du suchst." in body


def test_matches_page_never_shows_contact_details(app, client):
    _member(app, "me@example.com")
    _member(app, "partner@example.com", headline="Partner headline")
    login(client, "me@example.com")

    body = client.get("/profile/matches").get_data(as_text=True)

    assert "Partner headline" in body
    assert "partner@example.com" not in body
