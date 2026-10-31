"""Стандартный каталог услуг партнёра. Текст — контент для участников клуба (немецкий), правится в админке."""

from ..extensions import db
from ..models import Service

PROVIDER = "Andrii-IT · Partner des Clubs"
EMAIL = "info@andrii-it.de"
BENEFIT = "Kostenloses Erstgespräch für Club-Mitglieder"

# Встроенные ИИ-поиски по законам: услуга в каталоге открывает инструмент с предвыбранной страной (slug → код страны)
LAW_SERVICES = {"gesetze-schnellsuche-de": "DE", "gesetze-schnellsuche-il": "IL"}

DEFAULT_SERVICES = (
    dict(slug="gesetze-schnellsuche-de", sort=-2, title="KI-Schnellsuche im deutschen Recht",
         summary="Stell deine Frage in eigenen Worten und bekomme die passenden Vorschriften aus dem deutschen "
                 "Bundesrecht im Wortlaut, mit einer verständlichen Erläuterung. Spart Zeit bei der Vorbereitung "
                 "auf einen Termin mit einer Anwältin oder einem Anwalt.",
         description="Das Tool durchsucht rund 6.000 Bundesgesetze nach Bedeutung (nicht nur nach Stichworten) und "
                     "zitiert den Gesetzestext unverändert mit Quelle. Eine KI erklärt in einfachen Worten, was im "
                     "Text steht. Sie bewertet nie deinen Einzelfall und gibt keine Handlungsempfehlung — das ist "
                     "keine Rechtsberatung. Nutze es, um mit klaren Fragen und den richtigen Paragraphen in das "
                     "Gespräch mit einer Fachperson zu gehen. Für eine verbindliche Einschätzung schreib uns über "
                     "das Formular unten: Wir stellen den Kontakt zu einer Fachperson her.",
         benefits="Passende Paragraphen in Minuten statt Stunden\nGesetzestext im Wortlaut mit Quelle\n"
                  "Gut vorbereitet in den Termin mit einer Anwältin oder einem Anwalt\n"
                  "Keine Einzelfallbewertung, keine Rechtsberatung",
         price_hint="im Mitgliedsbeitrag enthalten (Kontingent je Tarif)"),
    dict(slug="gesetze-schnellsuche-il", sort=-1, title="KI-Schnellsuche im israelischen Recht",
         summary="Finde die passenden Vorschriften aus den wichtigsten israelischen Gesetzen (hebräischer Originaltext) "
                 "und lass dir erklären, was sie regeln — auf Deutsch oder Russisch. Zur Vorbereitung auf einen "
                 "Termin mit einer Anwältin oder einem Anwalt.",
         description="Das Tool durchsucht eine Auswahl der wichtigsten israelischen Gesetze (Grundgesetze, Vertrags-, "
                     "Gesellschafts-, Datenschutz-, Verbraucher-, Arbeits- und Steuerrecht u. a.). Du kannst deine "
                     "Frage auf Deutsch, Russisch, Englisch oder Hebräisch stellen. Der Gesetzestext erscheint im "
                     "hebräischen Original mit Link zur Quelle; die Erläuterung ist eine maschinelle Wiedergabe. "
                     "Wichtig: Die Sammlung ist eine inoffizielle Fassung (Open Law Book), verbindlich ist nur der "
                     "amtliche hebräische Wortlaut, und die Rechtsprechung ist nicht enthalten. Das ist keine "
                     "Rechtsberatung. Für eine verbindliche Einschätzung schreib uns über das Formular unten.",
         benefits="Fragen auf Deutsch, Russisch, Englisch oder Hebräisch\nHebräischer Gesetzestext mit Link zur Quelle\n"
                  "Gut vorbereitet in den Termin mit einer Anwältin oder einem Anwalt\n"
                  "Offen gelegt: inoffizielle Sammelfassung, ohne Rechtsprechung",
         price_hint="im Mitgliedsbeitrag enthalten (Kontingent je Tarif)"),
    dict(slug="ki-chatbots", sort=1, title="KI-Chatbots & digitale Assistenten",
         summary="Ein Assistent für deine Website, WhatsApp oder Telegram, der Anfragen beantwortet, Termine "
                 "vorbereitet und Leads qualifiziert — DSGVO-konform mit Hosting in der EU.",
         description="Wir entwickeln KI-Assistenten, die dein Angebot kennen und im Ton deiner Marke antworten: "
                     "auf der Website, in WhatsApp oder Telegram. Inklusive Datenschutzkonzept, Kennzeichnung nach "
                     "KI-Verordnung und Übergabe an dein Team, wenn ein Mensch gefragt ist.",
         benefits="Antworten rund um die Uhr, auch außerhalb der Bürozeiten\nWeniger Routinefragen im Postfach\n"
                  "Leads mit vollständigen Angaben statt halber E-Mails\nHosting in Deutschland, Auftragsverarbeitung inklusive",
         price_hint="Festpreis nach Erstgespräch"),
    dict(slug="cybersecurity-check", sort=2, title="Cybersecurity-Check für Selbstständige & KMU",
         summary="Wie angreifbar ist dein Unternehmen von außen? Ein Check deiner Website, E-Mail-Sicherheit und "
                 "öffentlich sichtbaren Systeme — mit verständlichem Bericht und klaren nächsten Schritten.",
         description="Wir prüfen, was Angreifer:innen über dein Unternehmen sehen: Website, Domain- und "
                     "E-Mail-Konfiguration (SPF, DKIM, DMARC), veraltete Software, offene Dienste. Du bekommst "
                     "einen Bericht ohne Fachchinesisch und eine priorisierte Maßnahmenliste.",
         benefits="Schwachstellen finden, bevor andere es tun\nNachweis für Versicherer und Kund:innen\n"
                  "Priorisierte Maßnahmen statt Panik",
         price_hint="ab Pauschalpreis, je nach Umfang"),
    dict(slug="nis2-dsgvo-compliance", sort=3, title="NIS2- und DSGVO-Check",
         summary="Bist du von NIS2 betroffen und was musst du konkret tun? Betroffenheitsprüfung, Lückenanalyse "
                 "und Maßnahmenplan für Geschäftsführung und Team.",
         description="Das NIS2-Umsetzungsgesetz erweitert den Kreis der regulierten Unternehmen deutlich und "
                     "nimmt die Geschäftsführung persönlich in die Pflicht. Wir klären Betroffenheit, bewerten den "
                     "Ist-Stand und erstellen einen umsetzbaren Plan.",
         benefits="Klarheit über Betroffenheit und Fristen\nDokumentation für Prüfungen\nSchulung der Geschäftsführung",
         price_hint="nach Aufwand"),
    dict(slug="ki-verordnung", sort=4, title="KI im Unternehmen — rechtssicher einführen",
         summary="Du nutzt ChatGPT, Chatbots oder KI-Tools? Wir prüfen, welche Pflichten aus KI-Verordnung und "
                 "DSGVO für dich gelten, und helfen bei Richtlinien und Schulung.",
         description="Seit 2025 gilt die Pflicht zur KI-Kompetenz für Unternehmen, die KI einsetzen, seit August "
                     "2026 die Transparenzpflichten für Chatbots. Wir machen eine Bestandsaufnahme, ordnen Risiken ein "
                     "und liefern Nutzungsrichtlinie und Schulung.",
         benefits="Überblick über eingesetzte KI-Tools\nNutzungsrichtlinie für dein Team\nNachweis der KI-Kompetenz",
         price_hint="Paketpreis"),
)


def seed_default_services() -> int:
    """Добавляет недостающие услуги по slug. Существующие (в том числе изменённые в админке) не трогает."""
    created = 0
    for item in DEFAULT_SERVICES:
        if Service.query.filter_by(slug=item["slug"]).first():
            continue
        db.session.add(Service(member_benefit=BENEFIT, provider_name=PROVIDER, provider_email=EMAIL,
                               active=True, **item))
        created += 1
    db.session.commit()
    return created
