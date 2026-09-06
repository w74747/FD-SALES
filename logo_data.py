"""
logo_data.py - Food Development Company Official Vector Logo Data
"""

LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 420 90" width="420" height="90">
  <rect width="100%" height="100%" fill="transparent"/>
  <g transform="translate(10, 10)">
    <!-- Symbol / Icon -->
    <circle cx="35" cy="35" r="32" fill="#F5F0FC" stroke="#E4D9F5" stroke-width="2"/>
    <path d="M 35 15 C 23.95 15 15 23.95 15 35 C 15 46.05 23.95 55 35 55 C 43.5 55 50.8 49.7 53.6 42 L 44.5 42 C 42.4 46.3 38.9 48.5 35 48.5 C 27.5 48.5 21.5 42.5 21.5 35 C 21.5 27.5 27.5 21.5 35 21.5 C 40.2 21.5 44.6 25.2 46.5 29.5 L 54.2 29.5 C 51.5 20.9 44 15 35 15 Z" fill="#3A056A"/>
    <circle cx="35" cy="35" r="5.5" fill="#7E22CE"/>
    <path d="M 48 20 C 49 23 48.5 27 46 29 C 44 26 44.5 22 48 20 Z" fill="#C194FB"/>

    <!-- Arabic Text -->
    <text x="85" y="34" font-family="'Cairo', 'Segoe UI', Tahoma, sans-serif" font-size="21" font-weight="900" fill="#3A056A">شركة تنمية الغذاء</text>
    
    <!-- English Subtitle -->
    <text x="86" y="54" font-family="'Cairo', 'Segoe UI', Tahoma, sans-serif" font-size="11" font-weight="700" fill="#7E22CE" letter-spacing="1.5">FOOD DEVELOPMENT CO.</text>
  </g>
</svg>"""

import base64

# صيغة Data-URI المباشرة المتوافقة مع كافة النوافذ والتقارير
LOGO_BASE64 = f"data:image/svg+xml;base64,{base64.b64encode(LOGO_SVG.strip().encode('utf-8')).decode('utf-8')}"
