"""Генератор единых по стилю векторных превью товаров (SVG 600x450).

Запуск из каталога backend:  python tools/generate_product_images.py
Создаёт media/products/<slug>.svg и обновляет ссылки ProductImage в fixtures.json.
Это стилизованные иллюстрации (не фотографии): они чёткие на любом экране, одинаково
оформлены и не «мылятся». Реальные фото можно положить поверх через админку."""
import json
import sys
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MEDIA = ROOT / "media" / "products"
FIXTURES = ROOT / "fixtures.json"

# pk товара -> (тип иллюстрации, slug файла, акцент, строка 1, строка 2, бейдж)
CATALOG = {
    1: ("gpu", "evga-rtx-cyberedition", "#76e04a", "EVGA GeForce RTX", "CyberEdition", "16 GB GDDR6X"),
    2: ("drone", "dji-spark-ai", "#38bdf8", "DJI Spark", "AI-Drone 4K", "AI-трекинг"),
    3: ("laptop", "surface-book-pro", "#60a5fa", "Surface Book Pro", "Ультрабук", "PixelSense"),
    4: ("keyboard", "keychron-cyber-q1", "#a78bfa", "Keychron Cyber Q1", "Механическая клавиатура", "Silent Red"),
    5: ("tablet", "surface-cyberpad", "#38bdf8", "Surface CyberPad", "Планшет-трансформер", "Blue Edition"),
    6: ("board", "jetson-dev-kit", "#76e04a", "NVIDIA Jetson", "Developer Kit", "Нейро-станция"),
    7: ("gpu", "rtx-5090-titan", "#76e04a", "GeForce RTX 5090", "AI Titan", "32 GB"),
    8: ("gpu", "asus-rog-strix-4080", "#f43f5e", "ASUS ROG Strix", "RTX 4080 Super", "16 GB"),
    9: ("gpu", "msi-4070ti-ventus", "#f97316", "MSI RTX 4070 Ti", "Ventus Silent", "12 GB"),
    10: ("gpu", "palit-rtx-4060", "#22d3ee", "Palit GeForce RTX 4060", "Compact", "8 GB"),
    11: ("board", "jetson-orin-nano", "#76e04a", "Jetson Orin Nano", "ИИ-ускоритель", "16 GB"),
    12: ("egpu", "razer-core-x", "#22c55e", "Razer Core X", "Thunderbolt eGPU", "Внешний бокс"),
    13: ("laptop", "macbook-pro-16-m4", "#94a3b8", "MacBook Pro 16", "M4 Max", "64 GB"),
    14: ("laptop", "rog-zephyrus-g16", "#f43f5e", "ROG Zephyrus G16", "Игровой ноутбук", "OLED"),
    15: ("laptop", "thinkpad-x1-carbon", "#ef4444", "ThinkPad X1 Carbon", "Linux Edition", "Ультрабук"),
    16: ("console", "steam-deck-oled", "#38bdf8", "Steam Deck OLED", "Портативная консоль", "1 TB"),
    17: ("laptop", "redmibook-pro-15", "#f97316", "RedmiBook Pro 15", "Ryzen 7", "Для учёбы и кода"),
    18: ("monitor", "lg-ultrawide-38", "#a78bfa", "LG UltraWide 38\"", "NanoIPS Curved", "Для кодинга"),
    19: ("mouse", "logitech-mx-master-4s", "#a78bfa", "Logitech MX Master 4S", "Бесшумная мышь", "Эргономика"),
    20: ("headphones", "sony-wh-1000xm6", "#22d3ee", "Sony WH-1000XM6", "ИИ-шумоподавление", "Беспроводные"),
    21: ("mic", "hyperx-quadcast-s", "#f43f5e", "HyperX QuadCast S", "Стримерский микрофон", "RGB"),
    22: ("lightbar", "xiaomi-light-bar", "#fbbf24", "Xiaomi Light Bar", "Лампа для монитора", "RGB"),
    23: ("deskmat", "cybermat-xl", "#22d3ee", "CyberMat XL", "Дескмат с зарядкой", "Qi"),
    24: ("drone", "dji-avata-2-pro", "#f97316", "DJI Avata 2 Pro", "FPV-дрон", "Очки погружения"),
    25: ("vr", "meta-quest-3s", "#38bdf8", "Meta Quest 3S", "VR / MR шлем", "Mixed Reality"),
    26: ("panels", "nanoleaf-cyber-shapes", "#e879f9", "Nanoleaf Cyber Shapes", "Световые панели", "9 шт."),
    27: ("power", "ecoflow-river-2-pro", "#22c55e", "EcoFlow River 2 Pro", "Электростанция", "800 W"),
    28: ("robovac", "roborock-s9-maxv", "#38bdf8", "Roborock S9 MaxV", "Робот-пылесос", "ИИ-лидар"),
    29: ("hub", "anker-prime-14in1", "#22d3ee", "Anker Prime", "Док-станция 14-в-1", "Display Hub"),
}

DARK, MID, LIGHT = "#0b1220", "#334155", "#e2e8f0"


def blades(cx, cy, r, color, n=7):
    out = []
    for i in range(n):
        out.append(f'<ellipse cx="{cx}" cy="{cy - r * 0.5:.1f}" rx="{r * 0.2:.1f}" ry="{r * 0.5:.1f}" '
                   f'fill="{color}" opacity="0.85" transform="rotate({i * 360 / n:.1f} {cx} {cy})"/>')
    return "".join(out)


def fan(cx, cy, r, a):
    return (f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#0f172a" stroke="{a}" stroke-width="3"/>'
            f'{blades(cx, cy, r * 0.9, "#1e293b")}<circle cx="{cx}" cy="{cy}" r="{r * 0.28:.1f}" fill="{a}"/>')


def icon(kind, a):
    if kind == "gpu":
        return (f'<rect x="130" y="150" width="340" height="120" rx="16" fill="#111827" stroke="{a}" stroke-width="3"/>'
                f'<rect x="112" y="156" width="22" height="108" rx="4" fill="#475569"/>'
                f'<rect x="170" y="268" width="180" height="12" rx="3" fill="#fbbf24"/>'
                + fan(205, 210, 44, a) + fan(300, 210, 44, a) + fan(395, 210, 44, a) +
                f'<rect x="150" y="160" width="300" height="6" rx="3" fill="{a}" opacity="0.5"/>')
    if kind == "laptop":
        return (f'<rect x="185" y="125" width="230" height="145" rx="12" fill="#0f172a" stroke="#94a3b8" stroke-width="4"/>'
                f'<rect x="197" y="137" width="206" height="121" rx="6" fill="url(#scr)"/>'
                f'<path d="M150 278h300l-16 20H166z" fill="#475569"/><rect x="262" y="278" width="76" height="6" rx="3" fill="#94a3b8"/>'
                f'<circle cx="300" cy="131" r="2.5" fill="#64748b"/>'
                f'<path d="M215 215l40-40 30 30 25-20 50 45H215z" fill="{a}" opacity="0.35"/>')
    if kind == "tablet":
        return (f'<rect x="195" y="120" width="210" height="150" rx="16" fill="#0f172a" stroke="#94a3b8" stroke-width="4"/>'
                f'<rect x="207" y="132" width="186" height="126" rx="8" fill="url(#scr)"/>'
                f'<rect x="215" y="282" width="170" height="16" rx="8" fill="{a}"/>'
                f'<line x1="430" y1="130" x2="470" y2="290" stroke="{LIGHT}" stroke-width="6" stroke-linecap="round"/>')
    if kind == "keyboard":
        keys = []
        for r in range(4):
            for c in range(13 if r < 3 else 9):
                x = 150 + c * 24 + (0 if r < 3 else 36)
                keys.append(f'<rect x="{x}" y="{165 + r * 28}" width="20" height="22" rx="4" '
                            f'fill="{a if (r + c) % 7 == 0 else "#1e293b"}" stroke="#475569"/>')
        return (f'<rect x="130" y="145" width="340" height="140" rx="16" fill="#0f172a" stroke="{a}" stroke-width="3"/>'
                + "".join(keys) +
                f'<rect x="200" y="256" width="200" height="18" rx="4" fill="#1e293b" stroke="#475569"/>')
    if kind == "mouse":
        return (f'<path d="M300 125C352 125 365 180 365 225C365 275 338 305 300 305C262 305 235 275 235 225C235 180 248 125 300 125Z" fill="#1e293b" stroke="{a}" stroke-width="4"/>'
                f'<path d="M300 125V200M235 200H365" stroke="#475569" stroke-width="3" fill="none"/>'
                f'<rect x="291" y="150" width="18" height="38" rx="9" fill="{a}"/>'
                f'<path d="M238 230c-8 20-4 40 8 52" stroke="{a}" stroke-width="5" fill="none" stroke-linecap="round" opacity="0.7"/>')
    if kind == "headphones":
        return (f'<path d="M205 235V210C205 150 245 120 300 120S395 150 395 210V235" fill="none" stroke="{LIGHT}" stroke-width="14" stroke-linecap="round"/>'
                f'<rect x="170" y="200" width="60" height="100" rx="26" fill="#1e293b" stroke="{a}" stroke-width="4"/>'
                f'<rect x="370" y="200" width="60" height="100" rx="26" fill="#1e293b" stroke="{a}" stroke-width="4"/>'
                f'<circle cx="200" cy="250" r="12" fill="{a}" opacity="0.8"/><circle cx="400" cy="250" r="12" fill="{a}" opacity="0.8"/>')
    if kind == "mic":
        bars = "".join(f'<line x1="262" y1="{150 + i * 16}" x2="338" y2="{150 + i * 16}" stroke="#475569" stroke-width="2"/>' for i in range(8))
        return (f'<rect x="258" y="125" width="84" height="140" rx="42" fill="#1e293b" stroke="{a}" stroke-width="4"/>{bars}'
                f'<path d="M225 215V235C225 278 255 300 300 300S375 278 375 235V215" fill="none" stroke="{LIGHT}" stroke-width="7" stroke-linecap="round"/>'
                f'<line x1="300" y1="300" x2="300" y2="330" stroke="{LIGHT}" stroke-width="7"/><rect x="255" y="326" width="90" height="10" rx="5" fill="#64748b"/>')
    if kind == "monitor":
        return (f'<path d="M115 150Q300 120 485 150V275Q300 250 115 275Z" fill="#0f172a" stroke="#94a3b8" stroke-width="4"/>'
                f'<path d="M128 160Q300 134 472 160V262Q300 240 128 262Z" fill="url(#scr)"/>'
                f'<rect x="288" y="262" width="24" height="40" fill="#64748b"/><rect x="240" y="300" width="120" height="10" rx="5" fill="#94a3b8"/>')
    if kind == "drone":
        arm = lambda x, y: (f'<line x1="300" y1="215" x2="{x}" y2="{y}" stroke="#64748b" stroke-width="8" stroke-linecap="round"/>'  # noqa: E731
                            f'<circle cx="{x}" cy="{y}" r="34" fill="{a}" opacity="0.18" stroke="{a}" stroke-width="2"/>'
                            f'<line x1="{x - 34}" y1="{y}" x2="{x + 34}" y2="{y}" stroke="{LIGHT}" stroke-width="4" stroke-linecap="round"/>')
        return (arm(190, 150) + arm(410, 150) + arm(190, 280) + arm(410, 280) +
                f'<ellipse cx="300" cy="215" rx="58" ry="34" fill="#1e293b" stroke="{a}" stroke-width="4"/>'
                f'<circle cx="300" cy="235" r="14" fill="#0f172a" stroke="{LIGHT}" stroke-width="3"/><circle cx="300" cy="235" r="5" fill="{a}"/>')
    if kind == "vr":
        return (f'<path d="M185 200C140 200 135 260 185 262M415 200C460 200 465 260 415 262" fill="none" stroke="#64748b" stroke-width="12" stroke-linecap="round"/>'
                f'<rect x="180" y="160" width="240" height="115" rx="36" fill="#1e293b" stroke="{a}" stroke-width="4"/>'
                f'<circle cx="250" cy="218" r="32" fill="#0f172a" stroke="{LIGHT}" stroke-width="3"/><circle cx="350" cy="218" r="32" fill="#0f172a" stroke="{LIGHT}" stroke-width="3"/>'
                f'<circle cx="250" cy="218" r="14" fill="{a}" opacity="0.8"/><circle cx="350" cy="218" r="14" fill="{a}" opacity="0.8"/>')
    if kind == "lightbar":
        return (f'<polygon points="170,170 430,170 520,330 80,330" fill="url(#beam)"/>'
                f'<rect x="150" y="150" width="300" height="24" rx="12" fill="{LIGHT}" stroke="{a}" stroke-width="3"/>'
                f'<rect x="170" y="156" width="260" height="8" rx="4" fill="{a}"/><rect x="280" y="130" width="40" height="22" rx="6" fill="#64748b"/>')
    if kind == "deskmat":
        return (f'<path d="M110 270L170 150H470L520 270Z" fill="#1e293b" stroke="{a}" stroke-width="4"/>'
                f'<path d="M130 262L180 160H460L504 262Z" fill="none" stroke="#475569" stroke-dasharray="6 6"/>'
                f'<ellipse cx="300" cy="215" rx="52" ry="26" fill="none" stroke="{a}" stroke-width="4"/>'
                f'<ellipse cx="300" cy="215" rx="30" ry="14" fill="{a}" opacity="0.35"/>')
    if kind == "console":
        return (f'<rect x="115" y="160" width="370" height="120" rx="54" fill="#1e293b" stroke="{a}" stroke-width="4"/>'
                f'<rect x="215" y="172" width="170" height="96" rx="10" fill="url(#scr)" stroke="#0f172a" stroke-width="4"/>'
                f'<circle cx="168" cy="205" r="20" fill="#0f172a" stroke="{LIGHT}" stroke-width="3"/><circle cx="432" cy="245" r="20" fill="#0f172a" stroke="{LIGHT}" stroke-width="3"/>'
                f'<path d="M150 242h36M168 224v36" stroke="{LIGHT}" stroke-width="7" stroke-linecap="round"/>'
                f'<circle cx="440" cy="205" r="7" fill="{a}"/><circle cx="460" cy="225" r="7" fill="{LIGHT}"/>')
    if kind == "power":
        return (f'<path d="M240 140V120h120v20" fill="none" stroke="#94a3b8" stroke-width="10" stroke-linecap="round"/>'
                f'<rect x="170" y="140" width="260" height="160" rx="22" fill="#1e293b" stroke="{a}" stroke-width="4"/>'
                f'<rect x="195" y="160" width="130" height="50" rx="8" fill="#0f172a" stroke="#475569"/>'
                f'<text x="260" y="196" font-family="Arial,sans-serif" font-size="26" font-weight="700" fill="{a}" text-anchor="middle">800W</text>'
                f'<circle cx="370" cy="185" r="22" fill="{a}" opacity="0.25" stroke="{a}" stroke-width="3"/>'
                + "".join(f'<rect x="{200 + i * 50}" y="240" width="34" height="34" rx="8" fill="#0f172a" stroke="#475569"/>' for i in range(4)))
    if kind == "robovac":
        return (f'<circle cx="300" cy="220" r="100" fill="#1e293b" stroke="{a}" stroke-width="4"/>'
                f'<circle cx="300" cy="220" r="82" fill="none" stroke="#475569" stroke-dasharray="4 8"/>'
                f'<circle cx="300" cy="212" r="30" fill="#0f172a" stroke="{LIGHT}" stroke-width="3"/><circle cx="300" cy="212" r="10" fill="{a}"/>'
                f'<path d="M232 288A100 100 0 0 0 368 288" fill="none" stroke="{a}" stroke-width="6" stroke-linecap="round" opacity="0.7"/>')
    if kind == "hub":
        ports = "".join(f'<rect x="{165 + i * 36}" y="198" width="24" height="14" rx="3" fill="#0f172a" stroke="{a}"/>'
                        f'<rect x="{165 + i * 36}" y="226" width="24" height="14" rx="3" fill="#0f172a" stroke="#475569"/>' for i in range(8))
        return (f'<rect x="140" y="170" width="320" height="100" rx="20" fill="#1e293b" stroke="{a}" stroke-width="4"/>{ports}'
                f'<path d="M460 220C510 220 520 280 560 280" fill="none" stroke="#94a3b8" stroke-width="6" stroke-linecap="round"/>')
    if kind == "board":
        pins = "".join(f'<rect x="{150 + i * 22}" y="278" width="10" height="16" fill="#fbbf24"/>' for i in range(14))
        fins = "".join(f'<rect x="{235 + i * 16}" y="160" width="8" height="66" rx="3" fill="#94a3b8"/>' for i in range(8))
        return (f'<rect x="130" y="140" width="340" height="140" rx="12" fill="#14532d" stroke="{a}" stroke-width="3"/>'
                f'<rect x="225" y="150" width="150" height="100" rx="8" fill="#0f172a" stroke="{a}" stroke-width="3"/>{fins}'
                f'<rect x="150" y="160" width="50" height="34" rx="4" fill="#0f172a" stroke="#64748b"/>'
                f'<rect x="150" y="205" width="50" height="34" rx="4" fill="#0f172a" stroke="#64748b"/>{pins}'
                f'<circle cx="440" cy="175" r="8" fill="{a}"/>')
    if kind == "egpu":
        vents = "".join(f'<line x1="{205 + i * 22}" y1="170" x2="{205 + i * 22}" y2="270" stroke="#475569" stroke-width="3"/>' for i in range(8))
        return (f'<rect x="170" y="140" width="260" height="160" rx="18" fill="#1e293b" stroke="{a}" stroke-width="4"/>{vents}'
                f'<path d="M310 160l-30 52h24l-12 46 44-62h-26z" fill="{a}"/>')
    if kind == "panels":
        def tri(x, y, c, o):
            return f'<polygon points="{x},{y} {x + 70},{y} {x + 35},{y + 60}" fill="{c}" opacity="{o}" stroke="{LIGHT}" stroke-width="2"/>'
        cols = ["#e879f9", "#38bdf8", "#22d3ee", "#a78bfa", "#f43f5e", "#fbbf24"]
        return "".join(tri(190 + col * 36, 150 + row * 62, cols[(row * 3 + col) % 6], 0.9 if (row + col) % 2 == 0 else 0.5)
                       for row in range(3) for col in range(5))
    return ""


def render(pk, kind, accent, l1, l2, badge):
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="600" height="450" viewBox="0 0 600 450" role="img" aria-label="{escape(l1)}">
<defs>
<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0b1220"/><stop offset="1" stop-color="#1e293b"/></linearGradient>
<radialGradient id="glow" cx="0.5" cy="0.5" r="0.5"><stop offset="0" stop-color="{accent}" stop-opacity="0.38"/><stop offset="1" stop-color="{accent}" stop-opacity="0"/></radialGradient>
<linearGradient id="scr" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{accent}" stop-opacity="0.55"/><stop offset="1" stop-color="#0b1220"/></linearGradient>
<linearGradient id="beam" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{accent}" stop-opacity="0.5"/><stop offset="1" stop-color="{accent}" stop-opacity="0"/></linearGradient>
<pattern id="grid" width="30" height="30" patternUnits="userSpaceOnUse"><path d="M30 0H0V30" fill="none" stroke="#94a3b8" stroke-opacity="0.07"/></pattern>
</defs>
<rect width="600" height="450" fill="url(#bg)"/><rect width="600" height="450" fill="url(#grid)"/>
<ellipse cx="300" cy="215" rx="260" ry="190" fill="url(#glow)"/>
<ellipse cx="300" cy="352" rx="170" ry="12" fill="#000" opacity="0.35"/>
<g transform="translate(0 -6)">{icon(kind, accent)}</g>
<rect x="28" y="26" width="{26 + 9 * len(badge)}" height="30" rx="15" fill="{accent}" fill-opacity="0.16" stroke="{accent}" stroke-opacity="0.8"/>
<text x="{28 + (26 + 9 * len(badge)) / 2}" y="46" font-family="'Segoe UI',Arial,sans-serif" font-size="13" font-weight="700" fill="{accent}" text-anchor="middle">{escape(badge)}</text>
<text x="300" y="392" font-family="'Segoe UI',Arial,sans-serif" font-size="26" font-weight="700" fill="#f8fafc" text-anchor="middle">{escape(l1)}</text>
<text x="300" y="420" font-family="'Segoe UI',Arial,sans-serif" font-size="16" fill="#94a3b8" text-anchor="middle">{escape(l2)}</text>
<text x="572" y="440" font-family="'Segoe UI',Arial,sans-serif" font-size="10" fill="#64748b" text-anchor="end">SokolykOff Shop</text>
</svg>
'''


PLACEHOLDER = '''<svg xmlns="http://www.w3.org/2000/svg" width="600" height="450" viewBox="0 0 600 450">
<rect width="600" height="450" fill="#111827"/><rect x="190" y="130" width="220" height="150" rx="16" fill="none" stroke="#475569" stroke-width="4" stroke-dasharray="10 8"/>
<path d="M215 255l50-60 38 40 28-28 54 48z" fill="#334155"/><circle cx="345" cy="175" r="14" fill="#334155"/>
<text x="300" y="330" font-family="Arial,sans-serif" font-size="18" fill="#64748b" text-anchor="middle">Фото скоро появится</text></svg>
'''


def main():
    MEDIA.mkdir(parents=True, exist_ok=True)
    for old in MEDIA.glob("*"):
        old.unlink()  # убираем учебные/мыльные картинки шаблона
    for pk, (kind, slug, accent, l1, l2, badge) in CATALOG.items():
        (MEDIA / f"{slug}.svg").write_text(render(pk, kind, accent, l1, l2, badge), encoding="utf-8")
    (ROOT / "static" / "shop").mkdir(parents=True, exist_ok=True)
    (ROOT / "static" / "shop" / "placeholder-product.svg").write_text(PLACEHOLDER, encoding="utf-8")

    data = json.loads(FIXTURES.read_text(encoding="utf-8-sig"))
    images = [x for x in data if x["model"] == "catalog.productimage"]
    products = {x["pk"]: x["fields"]["title"] for x in data if x["model"] == "catalog.product"}
    by_product = {}
    for img in images:
        by_product.setdefault(img["fields"]["product"], img)
    next_pk = max([i["pk"] for i in images] + [0]) + 1
    for pk, title in products.items():
        slug = CATALOG[pk][1]
        if pk in by_product:
            by_product[pk]["fields"].update(image=f"products/{slug}.svg", alt=title)
        else:
            data.append({"model": "catalog.productimage", "pk": next_pk, "fields": {
                "product": pk, "image": f"products/{slug}.svg", "alt": title}})
            next_pk += 1
    # лишние дубликаты картинок одного товара убираем
    keep = {id(v) for v in by_product.values()}
    data = [x for x in data if x["model"] != "catalog.productimage" or id(x) in keep or x["pk"] >= max(i["pk"] for i in images) + 1]
    FIXTURES.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Сгенерировано {len(CATALOG)} превью, fixtures.json обновлён", file=sys.stderr)


if __name__ == "__main__":
    main()
