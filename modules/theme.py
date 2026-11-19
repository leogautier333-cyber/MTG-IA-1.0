"""Thème néo-futuriste cyberpunk : feuille de style Streamlit et gabarit Plotly."""
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

PRIMARY, ACCENT, DEEP, YELLOW, GREEN, RED = "#ff1f3a", "#ff5a36", "#7d0012", "#fcee0a", "#39ff88", "#ff3860"
TEXT, MUTED, BACKGROUND, SURFACE = "#f6eeee", "#a08f92", "#000000", "#0a0507"
COLORWAY = [PRIMARY, "#ffffff", ACCENT, YELLOW, GREEN, DEEP]

STYLESHEET = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@500;700;900&family=Rajdhani:wght@400;500;600;700&family=Share+Tech+Mono&display=swap');

:root {{
    --bg: {BACKGROUND}; --surface: {SURFACE}; --surface-2: #140709;
    --primary: {PRIMARY}; --accent: {ACCENT}; --deep: {DEEP}; --yellow: {YELLOW};
    --green: {GREEN}; --red: {RED};
    --text: {TEXT}; --muted: {MUTED};
    --line: rgba(255, 31, 58, .28); --line-soft: rgba(255, 31, 58, .12);
    --glow-primary: 0 0 6px rgba(255,31,58,.65), 0 0 18px rgba(255,31,58,.35);
    --glow-accent: 0 0 6px rgba(255,31,58,.7), 0 0 20px rgba(255,31,58,.35);
    --cut: polygon(0 0, calc(100% - 14px) 0, 100% 14px, 100% 100%, 14px 100%, 0 calc(100% - 14px));
}}

.stApp {{
    color: var(--text); isolation: isolate;
    font-family: 'Rajdhani', sans-serif; font-size: 1.05rem;
    background: var(--bg);
}}
/* Arrière-plan : grille rouge qui dérive, halos qui respirent et faisceau de balayage */
.stApp::before {{
    content: ""; position: fixed; inset: -2px; z-index: -2; pointer-events: none;
    background:
        radial-gradient(ellipse 55% 45% at 10% 0%, rgba(255,31,58,.26), transparent 70%),
        radial-gradient(ellipse 50% 40% at 95% 100%, rgba(125,0,18,.34), transparent 70%),
        radial-gradient(ellipse 80% 60% at 50% 120%, rgba(255,31,58,.12), transparent 70%),
        linear-gradient(rgba(255,31,58,.085) 1px, transparent 1px) 0 0 / 48px 48px,
        linear-gradient(90deg, rgba(255,31,58,.085) 1px, transparent 1px) 0 0 / 48px 48px;
    animation: grid-drift 28s linear infinite, glow-breathe 7s ease-in-out infinite;
    -webkit-mask-image: radial-gradient(ellipse 120% 100% at 50% 40%, #000 35%, rgba(0,0,0,.35) 100%);
    mask-image: radial-gradient(ellipse 120% 100% at 50% 40%, #000 35%, rgba(0,0,0,.35) 100%);
}}
[data-testid="stAppViewContainer"]::before {{
    content: ""; position: fixed; left: 0; right: 0; top: 0; height: 220px; z-index: -1; pointer-events: none;
    background: linear-gradient(180deg, transparent, rgba(255,31,58,.10) 55%, rgba(255,90,54,.22) 92%, transparent);
    animation: scan-beam 9s cubic-bezier(.45,.05,.55,.95) infinite;
}}
@keyframes grid-drift {{
    from {{ background-position: 0 0, 0 0, 0 0, 0 0, 0 0; }}
    to {{ background-position: 0 0, 0 0, 0 0, 48px 48px, 48px 48px; }}
}}
@keyframes glow-breathe {{
    0%, 100% {{ opacity: .78; }}
    50% {{ opacity: 1; }}
}}
@keyframes scan-beam {{
    from {{ transform: translateY(-240px); }}
    to {{ transform: translateY(105vh); }}
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
::-webkit-scrollbar-thumb {{ background: linear-gradient(var(--primary), var(--accent)); border-radius: 0; }}

h1, h2, h3, h4 {{
    font-family: 'Orbitron', sans-serif !important; color: var(--text);
    letter-spacing: .08em; text-transform: uppercase;
}}
h1 {{
    font-size: 1.8rem !important; font-weight: 900 !important; padding-bottom: .7rem;
    color: var(--primary); text-shadow: var(--glow-primary);
    border-bottom: 1px solid var(--line); position: relative;
}}
h1::after {{
    content: ""; position: absolute; left: 0; bottom: -2px; width: 120px; height: 3px;
    background: var(--accent); box-shadow: var(--glow-accent);
}}
h2 {{ font-size: 1.35rem !important; color: var(--text); text-shadow: 0 0 12px rgba(255,31,58,.7); }}
h3 {{ font-size: 1.05rem !important; color: var(--accent); text-shadow: 0 0 10px rgba(255,31,58,.45); }}
p, label, .stCaption, li {{ color: var(--text); }}
[data-testid="stCaptionContainer"], .stCaption {{ color: var(--muted); font-family: 'Share Tech Mono', monospace; }}
a {{ color: var(--accent) !important; }}
code {{ font-family: 'Share Tech Mono', monospace; color: var(--yellow); background: rgba(252,238,10,.08); }}
hr {{ border-color: var(--line-soft) !important; }}

[data-testid="stMetric"] {{
    background: linear-gradient(145deg, rgba(22,8,11,.92), rgba(12,5,7,.85));
    padding: 1rem 1.1rem;
}}
[data-testid="stMetricLabel"] {{ color: var(--muted); text-transform: uppercase; letter-spacing: .12em; font-size: .8rem; }}
[data-testid="stMetricValue"] {{
    color: var(--text); font-family: 'Orbitron', sans-serif; font-weight: 700; font-size: 1.25rem;
    text-shadow: var(--glow-primary);
}}
[data-testid="stMetricValue"] > div, [data-testid="stMetricLabel"] p {{
    overflow: visible !important; text-overflow: clip !important; white-space: normal !important;
    overflow-wrap: anywhere;
}}

[role="tablist"], [data-testid="stRadio"] [role="radiogroup"] {{ gap: .5rem; }}
[role="tablist"] {{ border-bottom: 1px solid var(--line); margin-bottom: .4rem; }}
[role="tab"] {{
    font-family: 'Orbitron', sans-serif; font-size: .78rem; letter-spacing: .1em; text-transform: uppercase;
    color: var(--muted); padding: .8rem 1.1rem; border: 1px solid transparent; background: transparent;
    transition: all .2s ease;
}}
[role="tab"] p, [data-baseweb="tab"] [data-testid="stMarkdownContainer"] p {{
    font-family: 'Orbitron', sans-serif !important; font-size: .78rem !important;
    letter-spacing: .1em; text-transform: uppercase;
}}
[role="tab"]:hover {{ color: var(--primary); text-shadow: var(--glow-primary); }}
[role="tab"][aria-selected="true"] {{
    color: var(--primary) !important; text-shadow: var(--glow-primary);
    border: 1px solid var(--line) !important; border-bottom: 2px solid var(--accent) !important;
    background: linear-gradient(180deg, rgba(255,31,58,.12), transparent);
    box-shadow: 0 6px 18px -8px var(--accent);
}}
[data-baseweb="tab-highlight"] {{ background: var(--accent) !important; box-shadow: var(--glow-accent); }}
input[type="radio"] {{ accent-color: var(--accent) !important; }}
[data-testid="stRadio"] label p {{ font-family: 'Rajdhani', sans-serif; font-weight: 600; letter-spacing: .04em; }}

[data-testid="stExpander"], [class*="st-key-frame"] {{
    background: rgba(12,5,7,.66);
}}

/* Cadre unifié : trait néon à coins coupés (bordure en dégradé + diagonales) */
[data-testid="stMetric"], [data-testid="stExpander"], [class*="st-key-frame"],
[data-testid="stDataFrame"], [data-testid="stAlert"], [data-testid="stPlotlyChart"],
[data-testid="stForm"] {{
    position: relative; border: none !important; border-radius: 0 !important;
    clip-path: var(--cut);
    box-shadow: inset 0 0 28px rgba(255,31,58,.05) !important;
}}
[data-testid="stMetric"]::after, [data-testid="stExpander"]::after,
[class*="st-key-frame"]::after, [data-testid="stDataFrame"]::after,
[data-testid="stAlert"]::after, [data-testid="stPlotlyChart"]::after, [data-testid="stForm"]::after {{
    content: ""; position: absolute; inset: 0; padding: 1px; pointer-events: none; z-index: 3;
    background: linear-gradient(135deg, var(--primary), rgba(255,31,58,.25) 48%, var(--accent));
    -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
    -webkit-mask-composite: xor; mask-composite: exclude;
    transition: filter .25s ease;
}}
[data-testid="stMetric"]::before, [data-testid="stExpander"]::before,
[class*="st-key-frame"]::before, [data-testid="stDataFrame"]::before,
[data-testid="stAlert"]::before, [data-testid="stPlotlyChart"]::before, [data-testid="stForm"]::before {{
    content: ""; position: absolute; inset: 0; pointer-events: none; z-index: 3;
    background:
        linear-gradient(to top right, transparent calc(50% - .8px), var(--primary) calc(50% - .8px),
            var(--primary) calc(50% + .8px), transparent calc(50% + .8px)) top right / 14px 14px no-repeat,
        linear-gradient(to top right, transparent calc(50% - .8px), var(--accent) calc(50% - .8px),
            var(--accent) calc(50% + .8px), transparent calc(50% + .8px)) bottom left / 14px 14px no-repeat;
}}
[class*="st-key-frame"]:hover::after, [data-testid="stPlotlyChart"]:hover::after,
[data-testid="stMetric"]:hover::after {{ filter: drop-shadow(0 0 4px var(--primary)) brightness(1.35); }}

.stButton > button, [data-testid="stDownloadButton"] button, [data-testid="stFormSubmitButton"] button {{
    font-family: 'Orbitron', sans-serif; font-size: .78rem; letter-spacing: .12em; text-transform: uppercase;
    color: var(--primary); background: rgba(255,31,58,.06); border: 1px solid var(--primary); border-radius: 0;
    clip-path: var(--cut); box-shadow: inset 0 0 12px rgba(255,31,58,.15);
    transition: all .18s ease;
}}
.stButton > button:hover, [data-testid="stDownloadButton"] button:hover, [data-testid="stFormSubmitButton"] button:hover {{
    color: #000; background: var(--primary); border-color: var(--primary);
    box-shadow: var(--glow-primary); transform: translateY(-1px);
}}
.stButton > button[kind="primary"], [data-testid="stFormSubmitButton"] button[kind="primaryFormSubmit"] {{
    color: #fff; border-color: var(--accent); background: rgba(255,31,58,.16);
    box-shadow: inset 0 0 14px rgba(255,31,58,.3);
}}
.stButton > button[kind="primary"]:hover, [data-testid="stFormSubmitButton"] button[kind="primaryFormSubmit"]:hover {{
    background: var(--accent); color: #000; box-shadow: var(--glow-accent);
}}
.stButton > button:disabled {{ opacity: .35; box-shadow: none; }}

[data-baseweb="input"] > div, [data-baseweb="select"] > div, [data-baseweb="textarea"], [data-baseweb="base-input"] {{
    background: rgba(0,0,0,.9) !important; border-color: var(--line-soft) !important; border-radius: 0 !important;
    color: var(--text);
}}
[data-baseweb="input"] > div:focus-within, [data-baseweb="select"] > div:focus-within, [data-baseweb="textarea"]:focus-within {{
    border-color: var(--primary) !important; box-shadow: var(--glow-primary);
}}
[data-baseweb="popover"] ul {{ background: var(--surface) !important; border: 1px solid var(--line); }}
[data-baseweb="tag"] {{ background: rgba(255,31,58,.2) !important; border-radius: 0 !important; }}
[data-testid="stSlider"] [role="slider"] {{ background: var(--accent) !important; box-shadow: var(--glow-accent); }}
[data-testid="stFileUploaderDropzone"] {{ background: rgba(0,0,0,.8); border: 1px dashed var(--primary); border-radius: 0; }}

[data-testid="stAlert"] {{ background: rgba(12,5,7,.85); backdrop-filter: blur(4px); }}
[data-testid="stToast"] {{ border: 1px solid var(--primary); border-radius: 0; box-shadow: var(--glow-primary); background: var(--surface); }}
[data-testid="stImage"] img {{ border: 1px solid var(--line); box-shadow: 0 0 18px rgba(255,31,58,.18); }}
[data-testid="stProgress"] > div > div > div {{ background: linear-gradient(90deg, var(--primary), var(--accent)); box-shadow: var(--glow-primary); }}
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
    background: rgba(12,5,7,.6); border: 1px solid var(--line); padding: .8rem .9rem; margin-bottom: 1rem;
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
    0%, 100% {{ filter: drop-shadow(0 0 3px rgba(255,31,58,.55)); }}
    50% {{ filter: drop-shadow(0 0 9px rgba(255,31,58,.95)); }}
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
    h1, .js-plotly-plot .barlayer .point path, .stApp::before,
    [data-testid="stAppViewContainer"]::before {{ animation: none !important; }}
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
        colorway=COLORWAY, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,.55)",
        font={"family": "Rajdhani, sans-serif", "color": TEXT, "size": 14},
        title={"font": {"family": "Orbitron, sans-serif", "color": PRIMARY, "size": 16}},
        xaxis={"gridcolor": "rgba(255,31,58,.12)", "linecolor": "rgba(255,31,58,.4)",
               "zerolinecolor": "rgba(255,31,58,.4)"},
        yaxis={"gridcolor": "rgba(255,31,58,.12)", "linecolor": "rgba(255,31,58,.4)",
               "zerolinecolor": "rgba(255,31,58,.4)"},
        legend={"bgcolor": "rgba(0,0,0,0)"},
        hoverlabel={"bgcolor": SURFACE, "bordercolor": PRIMARY, "font": {"color": TEXT}},
        margin={"l": 40, "r": 20, "t": 50, "b": 40},
    )
    template.data.scatter = [go.Scatter(line={"width": 3})]
    return template


def apply_theme():
    """Injecte la feuille de style et rend le gabarit Plotly néon par défaut."""
    pio.templates["cyberpunk"] = _neon_template()
    pio.templates.default = "cyberpunk"
    st.markdown(STYLESHEET, unsafe_allow_html=True)
