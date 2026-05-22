import re

from app.models import Intent


class RuleBasedIntentParser:
    DATA_WORDS = (
        "data",
        "jumlah",
        "berapa",
        "penduduk",
        "kemiskinan",
        "pdrb",
        "inflasi",
        "ipm",
        "indeks",
        "pembangunan manusia",
        "pengangguran",
        "ketenagakerjaan",
        "tenaga kerja",
        "angkatan kerja",
        "bekerja",
        "tpt",
        "tpak",
        "sekolah",
        "kesehatan",
        "pertanian",
        "ekonomi",
        "publikasi",
        "katalog",
        "dalam angka",
        "luas",
        "lahan",
        "tanah",
        "podes",
        "potensi desa",
    )
    CONSULT_WORDS = ("konsultasi", "rekomendasi", "pst", "tamu", "layanan")
    ADMIN_WORDS = ("admin", "operator", "petugas", "pegawai", "cs", "customer service")
    EXIT_WORDS = ("keluar", "selesai", "stop", "akhiri")
    CANCEL_WORDS = ("batal", "batalkan")
    MENU_WORDS = ("menu", "mulai", "start")

    def classify(self, text: str) -> Intent:
        normalized = self._normalize(text)
        if normalized in {"1"}:
            return Intent.DATA_REQUEST
        if normalized in {"2"}:
            return Intent.CONSULTATION
        if normalized in {"3"}:
            return Intent.ADMIN
        if normalized in {"4"}:
            return Intent.EXIT
        if normalized in self.MENU_WORDS:
            return Intent.MENU
        if normalized in self.CANCEL_WORDS:
            return Intent.CANCEL
        if normalized in self.EXIT_WORDS:
            return Intent.EXIT
        if any(word in normalized for word in self.DATA_WORDS):
            return Intent.DATA_REQUEST
        if any(word in normalized for word in self.ADMIN_WORDS):
            return Intent.ADMIN
        if any(word in normalized for word in self.CONSULT_WORDS):
            return Intent.CONSULTATION
        return Intent.AMBIGUOUS

    def extract_data_query(self, text: str) -> str:
        normalized = self._normalize(text)
        cleaned = re.sub(r"\b(minta|cari|carikan|data|tentang|berapa|jumlah|saya|mau|ingin|tolong)\b", " ", normalized)
        query = re.sub(r"\s+", " ", cleaned).strip()
        return "" if normalized in {"data", "minta data", "cari data", "carikan data", "butuh data", "mau data", "ingin data"} else query

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"\s+", " ", text.strip().lower())
