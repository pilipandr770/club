"""Курируемые метаданные законов (slug на gesetze-im-internet.de -> название/категория).

slug — это часть URL вида .../<slug>/xml.zip.

Используется НЕ как фильтр (по умолчанию download_laws качает весь корпус),
а как тегирование: приоритетные для практики законы получают человекочитаемую
категорию в поле "category" каждого чанка — удобно для ручной навигации и для
фильтрации при поиске. Всё остальное из корпуса помечается как "Unclassified"
и всё равно попадает в базу — retrieval сам найдёт релевantные куски по смыслу
запроса, лишние законы не мешают.
"""

CATALOG = {
    # --- Общее гражданское / предпринимательское право ---
    "gg": ("Grundgesetz", "Verfassungsrecht"),
    "bgb": ("Bürgerliches Gesetzbuch", "Zivilrecht"),
    "bgbeg": ("Einführungsgesetz zum BGB", "Zivilrecht"),
    "hgb": ("Handelsgesetzbuch", "Handelsrecht"),
    "hgbeg": ("Einführungsgesetz zum HGB", "Handelsrecht"),
    "aktg": ("Aktiengesetz", "Gesellschaftsrecht"),
    "aktgeg": ("Einführungsgesetz zum AktG", "Gesellschaftsrecht"),
    "gmbhg": ("GmbH-Gesetz", "Gesellschaftsrecht"),
    "eggmbhg": ("Einführungsgesetz zum GmbHG", "Gesellschaftsrecht"),
    "geng": ("Genossenschaftsgesetz", "Gesellschaftsrecht"),
    "partgg": ("Partnerschaftsgesellschaftsgesetz", "Gesellschaftsrecht"),
    "umwg_1995": ("Umwandlungsgesetz", "Gesellschaftsrecht"),
    "inso": ("Insolvenzordnung", "Insolvenzrecht"),
    "eginso": ("Einführungsgesetz zur InsO", "Insolvenzrecht"),
    "zpo": ("Zivilprozessordnung", "Verfahrensrecht"),
    "gvg": ("Gerichtsverfassungsgesetz", "Verfahrensrecht"),
    "gvgeg": ("Einführungsgesetz zum GVG", "Verfahrensrecht"),
    "gkg_2004": ("Gerichtskostengesetz", "Verfahrensrecht"),
    "brao": ("Bundesrechtsanwaltsordnung", "Anwaltsrecht"),
    "rvg": ("Rechtsanwaltsvergütungsgesetz", "Anwaltsrecht"),
    "uwg_2004": ("Gesetz gegen den unlauteren Wettbewerb", "Wettbewerbsrecht"),
    "uklag": ("Unterlassungsklagengesetz", "Wettbewerbsrecht"),
    "prodhaftg": ("Produkthaftungsgesetz", "Zivilrecht"),
    "vvg_2008": ("Versicherungsvertragsgesetz", "Zivilrecht"),
    "beurkg": ("Beurkundungsgesetz", "Notarrecht"),

    # --- Уголовное право ---
    "stgb": ("Strafgesetzbuch", "Strafrecht"),
    "stgbeg": ("Einführungsgesetz zum StGB", "Strafrecht"),
    "stpo": ("Strafprozeßordnung", "Strafverfahrensrecht"),
    "stpoeg": ("Einführungsgesetz zur StPO", "Strafverfahrensrecht"),
    "owig_1968": ("Ordnungswidrigkeitengesetz", "Strafrecht"),
    "stvollzg": ("Strafvollzugsgesetz", "Strafrecht"),
    "jgg": ("Jugendgerichtsgesetz", "Strafrecht"),
    "wistrg_1954": ("Wirtschaftsstrafgesetz", "Wirtschaftsstrafrecht"),
    "btmg_1981": ("Betäubungsmittelgesetz", "Strafrecht"),

    # --- Интернет / разработка / SaaS / комплаенс ---
    "ddg": ("Digitale-Dienste-Gesetz", "IT-/Digitalrecht"),
    "tkg_2021": ("Telekommunikationsgesetz", "IT-/Digitalrecht"),
    "bdsg_2018": ("Bundesdatenschutzgesetz", "Datenschutzrecht"),
    "ttdsg": ("Telekommunikation-Telemedien-Datenschutz-Gesetz", "Datenschutzrecht"),
    "bsig_2025": ("BSI-Gesetz", "IT-Sicherheitsrecht"),
    "vdg": ("Vertrauensdienstegesetz", "IT-/Digitalrecht"),
    "urhg": ("Urheberrechtsgesetz", "Urheberrecht"),
    "geschgehg": ("Geschäftsgeheimnisgesetz", "Wirtschaftsrecht"),
    "kredwg": ("Kreditwesengesetz", "Finanzaufsichtsrecht"),
    "zag_2018": ("Zahlungsdiensteaufsichtsgesetz", "Finanzaufsichtsrecht"),
    "kmag": ("Kryptomärkteaufsichtsgesetz", "Finanzaufsichtsrecht"),
    "gwg_2017": ("Geldwäschegesetz", "Finanzaufsichtsrecht"),

    # --- Налоговое право ---
    "ao_1977": ("Abgabenordnung", "Steuerrecht"),
    "aoeg_1977": ("Einführungsgesetz zur Abgabenordnung", "Steuerrecht"),
    "estg": ("Einkommensteuergesetz", "Steuerrecht"),
    "kstg_1977": ("Körperschaftsteuergesetz", "Steuerrecht"),
    "gewstg": ("Gewerbesteuergesetz", "Steuerrecht"),
    "ustg_1980": ("Umsatzsteuergesetz", "Steuerrecht"),
    "bewg": ("Bewertungsgesetz", "Steuerrecht"),
    "erbstg_1974": ("Erbschaftsteuer- und Schenkungsteuergesetz", "Steuerrecht"),
    "grestg_1983": ("Grunderwerbsteuergesetz", "Steuerrecht"),
    "invstg_2018": ("Investmentsteuergesetz", "Steuerrecht"),
    "fgo": ("Finanzgerichtsordnung", "Steuerverfahrensrecht"),
    "stberg": ("Steuerberatungsgesetz", "Steuerrecht"),
    "kraftstg": ("Kraftfahrzeugsteuergesetz", "Steuerrecht"),
    "zollvg": ("Zollverwaltungsgesetz", "Steuerrecht"),
    "grstg_1973": ("Grundsteuergesetz", "Steuerrecht"),
    "solzg_1995": ("Solidaritätszuschlaggesetz", "Steuerrecht"),
}


def category_for(slug):
    entry = CATALOG.get(slug)
    return entry[1] if entry else "Unclassified"
