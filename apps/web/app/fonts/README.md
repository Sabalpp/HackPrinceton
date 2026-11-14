# Bundled fonts

The app uses Next's `next/font/local` loader so builds do not need Google Fonts
network access. These are the original, unmodified WOFF2 assets served by Google
Fonts for normal style and the variable weight range `400 700` on 2026-11-14.
The Latin and Devanagari subsets cover the app's English and Nepali interfaces;
the existing system font fallback handles other characters. No font runtime
dependency is required.

The official CSS request used to identify the assets was:
<https://fonts.googleapis.com/css2?family=Noto+Sans+Devanagari:wght@400..700&family=Plus+Jakarta+Sans:wght@400..700&display=swap>.

| File | Official source | SHA-256 |
| --- | --- | --- |
| `plus-jakarta-sans-latin.woff2` | [Google Fonts asset](https://fonts.gstatic.com/s/plusjakartasans/v12/LDIoaomQNQcsA88c7O9yZ4KMCoOg4Ko20yygg_vb.woff2) | `cd8db90cd950e26bc8761f65d323588bd5cd112d326d6d322bc7c8ea86771215` |
| `noto-sans-devanagari.woff2` | [Google Fonts asset](https://fonts.gstatic.com/s/notosansdevanagari/v33/TuG7UUFzXI5FBtUq5a8bjKYTZjtRU6Sgv3NaV_SNmI0b8QQCQmHN5TV_5Kl4-GIB.woff2) | `129e051f25c69c8d6e2725562b3916c5aabea2806dd6baca3a349ec99cea5140` |

The WOFF2 signatures and declared file lengths were checked on download. The
checksums above record these downloaded copies; they are not publisher-signed
checksums.

Both fonts use SIL Open Font License 1.1. Their original copyright notices and
full licenses are retained alongside the font files:

- [Plus Jakarta Sans license](OFL-Plus-Jakarta-Sans.txt), from
  [google/fonts](https://raw.githubusercontent.com/google/fonts/main/ofl/plusjakartasans/OFL.txt).
- [Noto Sans Devanagari license](OFL-Noto-Sans-Devanagari.txt), from
  [google/fonts](https://raw.githubusercontent.com/google/fonts/main/ofl/notosansdevanagari/OFL.txt).
