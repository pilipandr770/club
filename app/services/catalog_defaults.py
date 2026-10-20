"""Стандартный каталог услуг партнёра. Текст — контент для участников клуба (немецкий), правится в админке."""

from ..extensions import db
from ..models import Service

PROVIDER = "Andrii-IT · Partner des Clubs"
EMAIL = "info@andrii-it.de"
BENEFIT = "Kostenloses Erstgespräch für Club-Mitglieder"

DEFAULT_SERVICES = (
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
