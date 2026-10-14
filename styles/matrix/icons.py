"""
SVG-иконки темы Matrix.
Ключ — имя иконки. В модуле ставишь icon_name = "globe" — подхватится эта.
{color} заменяется на fg из темы.
"""

ICONS = {
    "websocket": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"
        fill="none" stroke="{color}" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round">
        <path d="M4 9h12"/>
        <path d="M13 6l3 3l-3 3"/>
        <path d="M20 15H8"/>
        <path d="M11 12l-3 3l3 3"/>
    </svg>''',

    "diff": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"
        fill="none" stroke="{color}" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round">
        <rect x="3" y="4" width="7" height="16" rx="1"/>
        <rect x="14" y="4" width="7" height="16" rx="1"/>
        <path d="M6 9h2"/>
        <path d="M7 8v2"/>
        <path d="M15 9h2"/>
        <path d="M5 14h3"/>
        <path d="M16 14h3"/>
    </svg>''',

    "dns": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"
        fill="none" stroke="{color}" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round">
        <circle cx="9" cy="9" r="6"/>
        <path d="M3 9h12"/>
        <path d="M9 3a6 6 0 0 1 0 12"/>
        <path d="M9 3a6 6 0 0 0 0 12"/>
        <circle cx="17" cy="17" r="3"/>
        <path d="M19.5 19.5L22 22"/>
    </svg>''',

    "encoder": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"
        fill="none" stroke="{color}" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round">
        <path d="M8 4c-2 0-3 1-3 3v10c0 2 1 3 3 3"/>
        <path d="M16 4c2 0 3 1 3 3v10c0 2-1 3-3 3"/>
        <path d="M10 12h4"/>
        <path d="M12 10l2 2l-2 2"/>
    </svg>''',

    "jwt": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"
        fill="none" stroke="{color}" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round">
        <rect x="2" y="6" width="20" height="12" rx="2"/>
        <path d="M9 6v12"/>
        <path d="M15 6v12"/>
    </svg>''',

    "login": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"
        fill="none" stroke="{color}" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round">
        <!-- дверь -->
        <rect x="12" y="3" width="9" height="18" rx="1.5"/>
        <circle cx="18.5" cy="12" r="0.8" fill="{color}" stroke="none"/>
        <!-- стрелка-вход -->
        <path d="M2 12h8"/>
        <path d="M7 9l3 3l-3 3"/>
    </svg>''',

    "port_scanner": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"
        fill="none" stroke="{color}" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round">
        <!-- сканируемые порты -->
        <rect x="1.5" y="4"    width="9.5" height="3" rx="0.6"/>
        <rect x="1.5" y="10.5" width="9.5" height="3" rx="0.6"/>
        <rect x="1.5" y="17"   width="9.5" height="3" rx="0.6"/>
        <!-- лупа -->
        <circle cx="16" cy="12" r="4"/>
        <path d="M18.9 14.9l2.6 2.6"/>
    </svg>''',

    "proxy": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"
        fill="none" stroke="{color}" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round">
        <rect x="4" y="4" width="16" height="16" rx="2"/>
        <path d="M8 9h8"/>
        <path d="M13 6l3 3l-3 3"/>
        <path d="M16 15H8"/>
        <path d="M11 12l-3 3l3 3"/>
    </svg>''',

    "reporter": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"
        fill="none" stroke="{color}" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round">
        <path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/>
        <path d="M14 3v6h6"/>
        <path d="M8 17v-4"/>
        <path d="M12 17v-8"/>
        <path d="M16 17v-6"/>
    </svg>''',

    "scanner": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"
        fill="none" stroke="{color}" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round">
        <path d="M3 7V5a2 2 0 0 1 2-2h2"/>
        <path d="M17 3h2a2 2 0 0 1 2 2v2"/>
        <path d="M21 17v2a2 2 0 0 1-2 2h-2"/>
        <path d="M7 21H5a2 2 0 0 1-2-2v-2"/>
        <path d="M3 12h18"/>
    </svg>''',

    "vault": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"
        fill="none" stroke="{color}" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round">
        <rect x="3" y="3" width="18" height="18" rx="2"/>
        <circle cx="11" cy="12" r="4"/>
        <path d="M11 8.5v1"/>
        <path d="M11 14.5v1"/>
        <path d="M7.5 12h1"/>
        <path d="M13.5 12h1"/>
        <path d="M18 9v6"/>
    </svg>''',

    "collections": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"
        fill="none" stroke="{color}" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round">
        <rect x="4" y="4" width="16" height="4" rx="1"/>
        <rect x="4" y="10" width="16" height="4" rx="1"/>
        <rect x="4" y="16" width="16" height="4" rx="1"/>
    </svg>''',

    "http_client": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"
        fill="none" stroke="{color}" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round">
        <rect x="2" y="4" width="13" height="16" rx="2"/>
        <path d="M2 8.5h13"/>
        <circle cx="5"  cy="6.25" r="0.8" fill="{color}" stroke="none"/>
        <circle cx="7.5" cy="6.25" r="0.8" fill="{color}" stroke="none"/>
        <path d="M5 12h7"/>
        <path d="M5 16h5"/>
        <path d="M17 12h4"/>
        <path d="M18 9l3 3l-3 3"/>
    </svg>''',

    "system_test": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"
        fill="none" stroke="{color}" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round">
        <path d="M10 3h4"/>
        <path d="M10 3v10l-4 8h12l-4-8V3"/>
        <path d="M9 17l2 2l4-4"/>
    </svg>''',
}