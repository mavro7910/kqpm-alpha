"""utils/styles.py — QPM Alpha (ui.py로 이관됨, 하위호환 유지)"""
from utils.ui import inject_all, GLOBAL_CSS

MAIN_CSS = GLOBAL_CSS

def inject_css():
    inject_all()
