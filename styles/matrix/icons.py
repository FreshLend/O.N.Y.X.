"""
SVG-иконки темы Matrix.
Ключ — имя иконки. В модуле ставишь icon_name = "globe" — подхватится эта.
{color} заменяется на fg из темы.
"""

ICONS = {
    "globe": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"
        fill="none" stroke="{color}" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="10"/>
        <path d="M2 12h20"/>
        <path d="M12 2a15 15 0 0 1 0 20a15 15 0 0 1 0-20z"/>
    </svg>''',

    "terminal": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"
        fill="none" stroke="{color}" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round">
        <rect x="3" y="4" width="18" height="16" rx="2"/>
        <path d="M7 9l3 3l-3 3"/>
        <path d="M13 15h4"/>
    </svg>''',

    "bolt": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"
        fill="{color}" stroke="none">
        <path d="M13 2L4 14h7l-1 8l9-12h-7z"/>
    </svg>''',

    "shield": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"
        fill="none" stroke="{color}" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 2l8 4v6c0 5-3.5 9-8 10c-4.5-1-8-5-8-10V6z"/>
    </svg>''',

    "code": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"
        fill="none" stroke="{color}" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round">
        <path d="M8 6l-6 6l6 6"/>
        <path d="M16 6l6 6l-6 6"/>
    </svg>''',

    "database": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"
        fill="none" stroke="{color}" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round">
        <ellipse cx="12" cy="5" rx="9" ry="3"/>
        <path d="M3 5v6c0 1.7 4 3 9 3s9-1.3 9-3V5"/>
        <path d="M3 11v6c0 1.7 4 3 9 3s9-1.3 9-3v-6"/>
    </svg>''',

    "settings": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"
        fill="none" stroke="{color}" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="3"/>
        <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3a1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5a1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8a1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1a1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3h.1a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5a1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>
    </svg>''',

    "back": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"
        fill="none" stroke="{color}" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round">
        <path d="M19 12H5"/>
        <path d="M12 19l-7-7l7-7"/>
    </svg>''',

    "play": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"
        fill="{color}" stroke="none">
        <path d="M6 4l14 8l-14 8z"/>
    </svg>''',

    "history": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"
        fill="none" stroke="{color}" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round">
        <path d="M3 12a9 9 0 1 0 3-6.7L3 8"/>
        <path d="M3 3v5h5"/>
        <path d="M12 7v5l3 2"/>
    </svg>''',

    "info": '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"
        fill="none" stroke="{color}" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round">
        <circle cx="12" cy="12" r="10"/>
        <line x1="12" y1="16" x2="12" y2="12"/>
        <line x1="12" y1="8" x2="12.01" y2="8"/>
    </svg>''',
}