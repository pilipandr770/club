"""Основные законы Израиля в корпусе клуба: slug, название страницы на Hebrew Wikisource, категория, псевдонимы.

Берём основные законы, а не весь свод: список расширяется строкой в этом файле и `flask laws-il-sync`.
Название страницы — как в ספר החוקים הפתוח (без «[נוסח חדש]»: квадратные скобки в названиях страниц запрещены).
Псевдонимы (латиницей, в нижнем регистре) нужны для прямой ссылки: «section 2 privacy protection law».
"""

from dataclasses import dataclass

SLUG_PREFIX = "il-"


@dataclass(frozen=True)
class IlLaw:
    slug: str
    page: str
    category: str
    aliases: tuple[str, ...] = ()


CATALOG = (
    IlLaw("il-basic-dignity", "חוק-יסוד: כבוד האדם וחירותו", "Verfassungsrecht",
          ("basic law: human dignity and liberty", "human dignity and liberty")),
    IlLaw("il-basic-occupation", "חוק-יסוד: חופש העיסוק", "Verfassungsrecht", ("basic law: freedom of occupation",)),
    IlLaw("il-interpretation", "חוק הפרשנות", "Zivilrecht", ("interpretation law",)),
    IlLaw("il-contracts-general", "חוק החוזים (חלק כללי)", "Zivilrecht", ("contracts law (general part)", "contracts law")),
    IlLaw("il-contracts-remedies", "חוק החוזים (תרופות בשל הפרת חוזה)", "Zivilrecht", ("contracts law (remedies for breach of contract)",)),
    IlLaw("il-standard-contracts", "חוק החוזים האחידים", "Verbraucherrecht", ("standard contracts law",)),
    IlLaw("il-consumer", "חוק הגנת הצרכן", "Verbraucherrecht", ("consumer protection law",)),
    IlLaw("il-sale", "חוק המכר", "Zivilrecht", ("sale law",)),
    IlLaw("il-rent", "חוק השכירות והשאילה", "Zivilrecht", ("rental and loan law", "lease law")),
    IlLaw("il-torts", "פקודת הנזיקין", "Zivilrecht", ("torts ordinance",)),
    IlLaw("il-unjust-enrichment", "חוק עשיית עושר ולא במשפט", "Zivilrecht", ("unjust enrichment law",)),
    IlLaw("il-agency", "חוק השליחות", "Zivilrecht", ("agency law",)),
    IlLaw("il-guarantee", "חוק הערבות", "Zivilrecht", ("guarantee law", "surety law")),
    IlLaw("il-succession", "חוק הירושה", "Erbrecht", ("succession law",)),
    IlLaw("il-land", "חוק המקרקעין", "Immobilienrecht", ("land law",)),
    IlLaw("il-brokers", "חוק המתווכים במקרקעין", "Immobilienrecht", ("real estate brokers law",)),
    IlLaw("il-companies", "חוק החברות", "Gesellschaftsrecht", ("companies law",)),
    IlLaw("il-arbitration", "חוק הבוררות", "Verfahrensrecht", ("arbitration law",)),
    IlLaw("il-privacy", "חוק הגנת הפרטיות", "Datenschutzrecht", ("privacy protection law", "protection of privacy law")),
    IlLaw("il-copyright", "חוק זכות יוצרים", "Urheberrecht", ("copyright law",)),
    IlLaw("il-communications", "חוק התקשורת (בזק ושידורים)", "Telekommunikationsrecht",
          ("communications law", "telecommunications law")),
    IlLaw("il-aml", "חוק איסור הלבנת הון", "Finanzaufsichtsrecht", ("prohibition on money laundering law", "aml law")),
    IlLaw("il-securities", "חוק ניירות ערך", "Finanzaufsichtsrecht", ("securities law",)),
    IlLaw("il-vat", "חוק מס ערך מוסף", "Steuerrecht", ("value added tax law", "vat law")),
    IlLaw("il-penal", "חוק העונשין", "Strafrecht", ("penal law",)),
    IlLaw("il-wage-protection", "חוק הגנת השכר", "Arbeitsrecht", ("wage protection law",)),
    IlLaw("il-working-hours", "חוק שעות עבודה ומנוחה", "Arbeitsrecht", ("hours of work and rest law",)),
    IlLaw("il-annual-leave", "חוק חופשה שנתית", "Arbeitsrecht", ("annual leave law",)),
    IlLaw("il-minimum-wage", "חוק שכר מינימום", "Arbeitsrecht", ("minimum wage law",)),
    IlLaw("il-severance", "חוק פיצויי פיטורים", "Arbeitsrecht", ("severance pay law",)),
    IlLaw("il-equal-opportunities", "חוק שוויון ההזדמנויות בעבודה", "Arbeitsrecht",
          ("equal employment opportunities law",)),
)

BY_SLUG = {law.slug: law for law in CATALOG}
BY_PAGE = {law.page: law for law in CATALOG}
CATEGORIES = tuple(dict.fromkeys(law.category for law in CATALOG))
