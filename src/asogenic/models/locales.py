"""The 50 App Store Connect locales. Source: reference/appro-docs/app-store-connect.md
§Supported Locales (Apple's March 2026 expansion). Treat as a default; the live list should be
derived from what ASC returns for a given app."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AppStoreLocale:
    id: str
    display_name: str

    @property
    def is_rtl(self) -> bool:
        return self.id in RTL_LOCALE_IDS


RTL_LOCALE_IDS: frozenset[str] = frozenset({"ar-SA", "he", "ur-PK"})

APP_STORE_LOCALES: tuple[AppStoreLocale, ...] = (
    AppStoreLocale("ar-SA", "Arabic (RTL)"),
    AppStoreLocale("bn-BD", "Bangla"),
    AppStoreLocale("ca", "Catalan"),
    AppStoreLocale("cs", "Czech"),
    AppStoreLocale("da", "Danish"),
    AppStoreLocale("de-DE", "German"),
    AppStoreLocale("el", "Greek"),
    AppStoreLocale("en-AU", "English (Australia)"),
    AppStoreLocale("en-CA", "English (Canada)"),
    AppStoreLocale("en-GB", "English (United Kingdom)"),
    AppStoreLocale("en-US", "English (U.S.)"),
    AppStoreLocale("es-ES", "Spanish (Spain)"),
    AppStoreLocale("es-MX", "Spanish (Mexico)"),
    AppStoreLocale("fi", "Finnish"),
    AppStoreLocale("fr-CA", "French (Canada)"),
    AppStoreLocale("fr-FR", "French"),
    AppStoreLocale("gu-IN", "Gujarati"),
    AppStoreLocale("he", "Hebrew (RTL)"),
    AppStoreLocale("hi", "Hindi"),
    AppStoreLocale("hr", "Croatian"),
    AppStoreLocale("hu", "Hungarian"),
    AppStoreLocale("id", "Indonesian"),
    AppStoreLocale("it", "Italian"),
    AppStoreLocale("ja", "Japanese"),
    AppStoreLocale("kn-IN", "Kannada"),
    AppStoreLocale("ko", "Korean"),
    AppStoreLocale("ml-IN", "Malayalam"),
    AppStoreLocale("mr-IN", "Marathi"),
    AppStoreLocale("ms", "Malay"),
    AppStoreLocale("nl-NL", "Dutch"),
    AppStoreLocale("no", "Norwegian"),
    AppStoreLocale("or-IN", "Odia"),
    AppStoreLocale("pa-IN", "Punjabi"),
    AppStoreLocale("pl", "Polish"),
    AppStoreLocale("pt-BR", "Portuguese (Brazil)"),
    AppStoreLocale("pt-PT", "Portuguese (Portugal)"),
    AppStoreLocale("ro", "Romanian"),
    AppStoreLocale("ru", "Russian"),
    AppStoreLocale("sk", "Slovak"),
    AppStoreLocale("sl-SI", "Slovenian"),
    AppStoreLocale("sv", "Swedish"),
    AppStoreLocale("ta-IN", "Tamil"),
    AppStoreLocale("te-IN", "Telugu"),
    AppStoreLocale("th", "Thai"),
    AppStoreLocale("tr", "Turkish"),
    AppStoreLocale("uk", "Ukrainian"),
    AppStoreLocale("ur-PK", "Urdu (RTL)"),
    AppStoreLocale("vi", "Vietnamese"),
    AppStoreLocale("zh-Hans", "Chinese (Simplified)"),
    AppStoreLocale("zh-Hant", "Chinese (Traditional)"),
)

LOCALES_BY_ID: dict[str, AppStoreLocale] = {loc.id: loc for loc in APP_STORE_LOCALES}

LOCALE_ALIASES: dict[str, str] = {
    "it-IT": "it",
    "ja-JP": "ja",
    "ko-KR": "ko",
}


def is_valid_locale(locale_id: str) -> bool:
    return locale_id in LOCALES_BY_ID or locale_id in LOCALE_ALIASES


def canonical_locale_id(locale_id: str) -> str:
    return LOCALE_ALIASES.get(locale_id, locale_id)
