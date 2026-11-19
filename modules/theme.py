"""Thème néo-futuriste cyberpunk : feuille de style Streamlit et gabarit Plotly."""
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

CYAN, ACCENT, YELLOW, GREEN, RED = "#00f0ff", "#ff2a3d", "#fcee0a", "#39ff88", "#ff3860"
ORANGE = "#ff8a1f"
TEXT, MUTED, BACKGROUND, SURFACE = "#e6f7ff", "#7f93b5", "#05060f", "#0a0d1c"
COLORWAY = [CYAN, ACCENT, YELLOW, GREEN, ORANGE, RED]

STYLESHEET = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@500;700;900&family=Rajdhani:wght@400;500;600;700&family=Share+Tech+Mono&display=swap');

:root {{
    --bg: {BACKGROUND}; --surface: {SURFACE}; --surface-2: #10142a;
    --cyan: {CYAN}; --accent: {ACCENT}; --yellow: {YELLOW};
    --green: {GREEN}; --red: {RED}; --orange: {ORANGE};
    --text: {TEXT}; --muted: {MUTED};
    --line: rgba(0, 240, 255, .28); --line-soft: rgba(0, 240, 255, .12);
    --glow-cyan: 0 0 6px rgba(0,240,255,.65), 0 0 18px rgba(0,240,255,.35);
    --glow-accent: 0 0 6px rgba(255,42,61,.7), 0 0 20px rgba(255,42,61,.35);
    --cut: polygon(0 0, calc(100% - 14px) 0, 100% 14px, 100% 100%, 14px 100%, 0 calc(100% - 14px));
}}

.stApp {{
    color: var(--text);
    font-family: 'Rajdhani', sans-serif; font-size: 1.05rem;
    background:
        radial-gradient(ellipse 60% 40% at 15% 0%, rgba(255,42,61,.14), transparent 70%),
        radial-gradient(ellipse 55% 45% at 90% 10%, rgba(0,240,255,.12), transparent 70%),
        linear-gradient(rgba(0,240,255,.045) 1px, transparent 1px) 0 0 / 44px 44px,
        linear-gradient(90deg, rgba(0,240,255,.045) 1px, transparent 1px) 0 0 / 44px 44px,
        var(--bg);
    background-attachment: fixed;
}}
.stApp::after {{
    content: ""; position: fixed; inset: 0; pointer-events: none; z-index: 9999;
    background: repeating-linear-gradient(0deg, rgba(0,0,0,.18) 0, rgba(0,0,0,.18) 1px, transparent 1px, transparent 3px);
    mix-blend-mode: multiply; opacity: .22;
}}
[data-testid="stHeader"] {{ background: transparent; }}
.block-container {{ max-width: 1500px; padding: 2rem 3rem 4rem; }}
::selection {{ background: var(--accent); color: #000; }}
::-webkit-scrollbar {{ width: 10px; height: 10px; }}
::-webkit-scrollbar-track {{ background: var(--bg); }}
::-webkit-scrollbar-thumb {{ background: linear-gradient(var(--cyan), var(--accent)); border-radius: 0; }}

h1, h2, h3, h4 {{
    font-family: 'Orbitron', sans-serif !important; color: var(--text);
    letter-spacing: .08em; text-transform: uppercase;
}}
h1 {{
    font-size: 1.8rem !important; font-weight: 900 !important; padding-bottom: .7rem;
    color: var(--cyan); text-shadow: var(--glow-cyan);
    border-bottom: 1px solid var(--line); position: relative;
}}
h1::after {{
    content: ""; position: absolute; left: 0; bottom: -2px; width: 120px; height: 3px;
    background: var(--accent); box-shadow: var(--glow-accent);
}}
h2 {{ font-size: 1.35rem !important; color: var(--cyan); text-shadow: 0 0 10px rgba(0,240,255,.45); }}
h3 {{ font-size: 1.05rem !important; color: var(--accent); text-shadow: 0 0 10px rgba(255,42,61,.45); }}
p, label, .stCaption, li {{ color: var(--text); }}
[data-testid="stCaptionContainer"], .stCaption {{ color: var(--muted); font-family: 'Share Tech Mono', monospace; }}
a {{ color: var(--cyan) !important; }}
code {{ font-family: 'Share Tech Mono', monospace; color: var(--yellow); background: rgba(252,238,10,.08); }}
hr {{ border-color: var(--line-soft) !important; }}

[data-testid="stMetric"] {{
    background: linear-gradient(145deg, rgba(16,20,42,.92), rgba(10,13,28,.85));
    padding: 1rem 1.1rem;
}}
[data-testid="stMetricLabel"] {{ color: var(--muted); text-transform: uppercase; letter-spacing: .12em; font-size: .8rem; }}
[data-testid="stMetricValue"] {{
    color: var(--cyan); font-family: 'Orbitron', sans-serif; font-weight: 700; font-size: 1.25rem;
    text-shadow: var(--glow-cyan);
}}
[data-testid="stMetricValue"] > div, [data-testid="stMetricLabel"] p {{
    overflow: visible !important; text-overflow: clip !important; white-space: normal !important;
    overflow-wrap: anywhere;
}}

[role="tablist"], [data-testid="stRadio"] [role="radiogroup"] {{ gap: .5rem; }}
[role="tablist"] {{ border-bottom: 1px solid var(--line); margin-bottom: .4rem; }}
button[role="tab"] {{
    font-family: 'Orbitron', sans-serif; font-size: .78rem; letter-spacing: .1em; text-transform: uppercase;
    color: var(--muted); padding: .8rem 1.1rem; border: 1px solid transparent; background: transparent;
    transition: all .2s ease;
}}
button[role="tab"] p, [data-baseweb="tab"] [data-testid="stMarkdownContainer"] p {{
    font-family: 'Orbitron', sans-serif !important; font-size: .78rem !important;
    letter-spacing: .1em; text-transform: uppercase;
}}
button[role="tab"]:hover {{ color: var(--cyan); text-shadow: var(--glow-cyan); }}
button[role="tab"][aria-selected="true"] {{
    color: var(--cyan) !important; text-shadow: var(--glow-cyan);
    border: 1px solid var(--line) !important; border-bottom: 2px solid var(--accent) !important;
    background: linear-gradient(180deg, rgba(0,240,255,.12), transparent);
    box-shadow: 0 6px 18px -8px var(--accent);
}}
[data-baseweb="tab-highlight"] {{ background: var(--accent) !important; box-shadow: var(--glow-accent); }}
input[type="radio"] {{ accent-color: var(--accent) !important; }}
[data-testid="stRadio"] label p {{ font-family: 'Rajdhani', sans-serif; font-weight: 600; letter-spacing: .04em; }}

[data-testid="stExpander"], [class*="st-key-frame"] {{
    background: rgba(10,13,28,.66);
}}

/* Cadre unifié : trait néon à coins coupés (bordure en dégradé + diagonales) */
[data-testid="stMetric"], [data-testid="stExpander"], [class*="st-key-frame"],
[data-testid="stDataFrame"], [data-testid="stAlert"], [data-testid="stPlotlyChart"],
[data-testid="stForm"] {{
    position: relative; border: none !important; border-radius: 0 !important;
    clip-path: var(--cut);
    box-shadow: inset 0 0 28px rgba(0,240,255,.05) !important;
}}
[data-testid="stMetric"]::after, [data-testid="stExpander"]::after,
[class*="st-key-frame"]::after, [data-testid="stDataFrame"]::after,
[data-testid="stAlert"]::after, [data-testid="stPlotlyChart"]::after, [data-testid="stForm"]::after {{
    content: ""; position: absolute; inset: 0; padding: 1px; pointer-events: none; z-index: 3;
    background: linear-gradient(135deg, var(--cyan), rgba(0,240,255,.22) 48%, var(--accent));
    -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
    -webkit-mask-composite: xor; mask-composite: exclude;
    transition: filter .25s ease;
}}
[data-testid="stMetric"]::before, [data-testid="stExpander"]::before,
[class*="st-key-frame"]::before, [data-testid="stDataFrame"]::before,
[data-testid="stAlert"]::before, [data-testid="stPlotlyChart"]::before, [data-testid="stForm"]::before {{
    content: ""; position: absolute; inset: 0; pointer-events: none; z-index: 3;
    background:
        linear-gradient(to top right, transparent calc(50% - .8px), var(--cyan) calc(50% - .8px),
            var(--cyan) calc(50% + .8px), transparent calc(50% + .8px)) top right / 14px 14px no-repeat,
        linear-gradient(to top right, transparent calc(50% - .8px), var(--accent) calc(50% - .8px),
            var(--accent) calc(50% + .8px), transparent calc(50% + .8px)) bottom left / 14px 14px no-repeat;
}}
[class*="st-key-frame"]:hover::after, [data-testid="stPlotlyChart"]:hover::after,
[data-testid="stMetric"]:hover::after {{ filter: drop-shadow(0 0 4px var(--cyan)) brightness(1.35); }}

.stButton > button, [data-testid="stDownloadButton"] button, [data-testid="stFormSubmitButton"] button {{
    font-family: 'Orbitron', sans-serif; font-size: .78rem; letter-spacing: .12em; text-transform: uppercase;
    color: var(--cyan); background: rgba(0,240,255,.06); border: 1px solid var(--cyan); border-radius: 0;
    clip-path: var(--cut); box-shadow: inset 0 0 12px rgba(0,240,255,.15);
    transition: all .18s ease;
}}
.stButton > button:hover, [data-testid="stDownloadButton"] button:hover, [data-testid="stFormSubmitButton"] button:hover {{
    color: #000; background: var(--cyan); border-color: var(--cyan);
    box-shadow: var(--glow-cyan); transform: translateY(-1px);
}}
.stButton > button[kind="primary"], [data-testid="stFormSubmitButton"] button[kind="primaryFormSubmit"] {{
    color: #fff; border-color: var(--accent); background: rgba(255,42,61,.16);
    box-shadow: inset 0 0 14px rgba(255,42,61,.3);
}}
.stButton > button[kind="primary"]:hover, [data-testid="stFormSubmitButton"] button[kind="primaryFormSubmit"]:hover {{
    background: var(--accent); color: #000; box-shadow: var(--glow-accent);
}}
.stButton > button:disabled {{ opacity: .35; box-shadow: none; }}

[data-baseweb="input"] > div, [data-baseweb="select"] > div, [data-baseweb="textarea"], [data-baseweb="base-input"] {{
    background: rgba(5,6,15,.9) !important; border-color: var(--line-soft) !important; border-radius: 0 !important;
    color: var(--text);
}}
[data-baseweb="input"] > div:focus-within, [data-baseweb="select"] > div:focus-within, [data-baseweb="textarea"]:focus-within {{
    border-color: var(--cyan) !important; box-shadow: var(--glow-cyan);
}}
[data-baseweb="popover"] ul {{ background: var(--surface) !important; border: 1px solid var(--line); }}
[data-baseweb="tag"] {{ background: rgba(255,42,61,.2) !important; border-radius: 0 !important; }}
[data-testid="stSlider"] [role="slider"] {{ background: var(--accent) !important; box-shadow: var(--glow-accent); }}
[data-testid="stFileUploaderDropzone"] {{ background: rgba(5,6,15,.8); border: 1px dashed var(--cyan); border-radius: 0; }}

[data-testid="stAlert"] {{ background: rgba(10,13,28,.85); backdrop-filter: blur(4px); }}
[data-testid="stToast"] {{ border: 1px solid var(--cyan); border-radius: 0; box-shadow: var(--glow-cyan); background: var(--surface); }}
[data-testid="stImage"] img {{ border: 1px solid var(--line); box-shadow: 0 0 18px rgba(0,240,255,.18); }}
[data-testid="stProgress"] > div > div > div {{ background: linear-gradient(90deg, var(--cyan), var(--accent)); box-shadow: var(--glow-cyan); }}
[data-testid="stSpinner"] i {{ border-top-color: var(--accent) !important; }}

.margin-badge {{
    display: flex; align-items: center; justify-content: space-between; gap: .45rem;
    padding: .5rem .8rem; border: 1px solid transparent; font-family: 'Share Tech Mono', monospace;
    font-size: .8rem; letter-spacing: .04em; margin-top: .65rem; width: 100%; box-sizing: border-box;
    clip-path: var(--cut);
}}
.margin-badge.profit-badge {{
    background: linear-gradient(135deg, rgba(57,255,136,.18), rgba(57,255,136,.04));
    border-color: rgba(57,255,136,.55); color: var(--green); text-shadow: 0 0 8px rgba(57,255,136,.6);
}}
.margin-badge.loss-badge {{
    background: linear-gradient(135deg, rgba(255,56,96,.18), rgba(255,56,96,.04));
    border-color: rgba(255,56,96,.55); color: var(--red); text-shadow: 0 0 8px rgba(255,56,96,.6);
}}
.badge-label {{ opacity: .8; text-transform: uppercase; letter-spacing: .16em; font-size: .62rem; }}
.badge-value {{ font-size: .9rem; font-weight: 700; }}
.badge-sub {{ opacity: .9; }}

.filter-toolbar {{
    background: rgba(10,13,28,.6); border: 1px solid var(--line); padding: .8rem .9rem; margin-bottom: 1rem;
}}
.card-meta {{ font-family: 'Share Tech Mono', monospace; font-size: .76rem; color: var(--muted); line-height: 1.7; }}
.card-title-row {{ display: flex; justify-content: space-between; align-items: center; gap: .75rem; margin-bottom: .25rem; }}
.card-title-row .stMarkdown {{ margin-bottom: 0; }}

@keyframes bar-grow {{
    from {{ transform: scaleX(0); opacity: .2; }}
    60% {{ opacity: 1; }}
    to {{ transform: scaleX(1); opacity: 1; }}
}}
@keyframes bar-pulse {{
    0%, 100% {{ filter: drop-shadow(0 0 3px rgba(0,240,255,.55)); }}
    50% {{ filter: drop-shadow(0 0 9px rgba(0,240,255,.95)); }}
}}
.js-plotly-plot .barlayer .point path {{
    transform-box: fill-box; transform-origin: 0 50%;
    animation: bar-grow .9s cubic-bezier(.16,.84,.3,1) both, bar-pulse 3.2s ease-in-out .9s infinite;
}}
.js-plotly-plot .barlayer .point:nth-child(1) path {{ animation-delay: 0s, .9s; }}
.js-plotly-plot .barlayer .point:nth-child(2) path {{ animation-delay: .1s, 1s; }}
.js-plotly-plot .barlayer .point:nth-child(3) path {{ animation-delay: .2s, 1.1s; }}
.js-plotly-plot .barlayer .point:nth-child(4) path {{ animation-delay: .3s, 1.2s; }}
.js-plotly-plot .barlayer .point:nth-child(5) path {{ animation-delay: .4s, 1.3s; }}
.js-plotly-plot .barlayer .point:nth-child(n+6) path {{ animation-delay: .5s, 1.4s; }}

@keyframes neon-flicker {{
    0%, 19%, 22%, 62%, 64%, 100% {{ opacity: 1; }}
    20%, 21%, 63% {{ opacity: .72; }}
}}
h1 {{ animation: neon-flicker 6s infinite; }}
@media (prefers-reduced-motion: reduce) {{
    h1, .js-plotly-plot .barlayer .point path {{ animation: none !important; }}
    .stApp::after {{ display: none; }}
}}
@media (max-width: 760px) {{
    .block-container {{ padding: 1rem 1rem 3rem; }}
    h1 {{ font-size: 1.4rem !important; }}
}}
</style>
"""


def _neon_template():
    template = go.layout.Template()
    template.layout = go.Layout(
        colorway=COLORWAY, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(5,6,15,.55)",
        font={"family": "Rajdhani, sans-serif", "color": TEXT, "size": 14},
        title={"font": {"family": "Orbitron, sans-serif", "color": CYAN, "size": 16}},
        xaxis={"gridcolor": "rgba(0,240,255,.12)", "linecolor": "rgba(0,240,255,.4)",
               "zerolinecolor": "rgba(255,42,61,.4)"},
        yaxis={"gridcolor": "rgba(0,240,255,.12)", "linecolor": "rgba(0,240,255,.4)",
               "zerolinecolor": "rgba(255,42,61,.4)"},
        legend={"bgcolor": "rgba(0,0,0,0)"},
        hoverlabel={"bgcolor": SURFACE, "bordercolor": CYAN, "font": {"color": TEXT}},
        margin={"l": 40, "r": 20, "t": 50, "b": 40},
    )
    template.data.scatter = [go.Scatter(line={"width": 3})]
    return template


def apply_theme():
    """Injecte la feuille de style et rend le gabarit Plotly néon par défaut."""
    pio.templates["cyberpunk"] = _neon_template()
    pio.templates.default = "cyberpunk"
    st.markdown(STYLESHEET, unsafe_allow_html=True)
