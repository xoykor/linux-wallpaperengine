"""Visual system for the GTK4 desktop application.

The palette deliberately uses opaque surfaces: previews stay vivid while labels,
controls, and focus rings remain legible on a composited desktop.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk


CSS = """
@define-color canvas #0b1019;
@define-color sidebar #101722;
@define-color surface #151e2a;
@define-color surface_raised #1b2736;
@define-color surface_hover #243447;
@define-color outline #2c3b4c;
@define-color text #f3f7fc;
@define-color text_muted #a9b8c9;
@define-color text_dim #879bb0;
@define-color accent #76e1d1;
@define-color accent_hover #9af2e4;
@define-color accent_ink #0c2227;
@define-color danger #f29caa;
@define-color shadow #050910;

window,
dialog,
popover {
  background-color: @canvas;
  color: @text;
  font-family: "Inter", "Noto Sans", "Noto Sans CJK JP", "Noto Sans CJK SC",
               "Noto Sans Devanagari", "Cantarell", sans-serif;
}

window.background,
.app-shell {
  background-color: @canvas;
}

headerbar {
  min-height: 62px;
  padding: 7px 14px;
  background-color: @sidebar;
  background-image: none;
  border: none;
  border-bottom: 1px solid @outline;
  box-shadow: none;
}

headerbar.topbar { background-color: #0e1621; }

headerbar .title,
.sidebar-brand {
  color: @text;
  font-size: 17px;
  font-weight: 800;
}

.brand-title {
  color: @text_muted;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 1.35px;
}

.brand-overline {
  color: @accent;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 2px;
}

.brand-display {
  color: @text;
  font-size: 32px;
  font-weight: 800;
  letter-spacing: -1px;
}

label { color: @text; }

.page-kicker {
  color: @accent;
  font-size: 11px;
  font-weight: 800;
  letter-spacing: 1.3px;
}

.page-title {
  color: @text;
  font-size: 28px;
  font-weight: 800;
  letter-spacing: -0.6px;
}

.page-subtitle,
.subtle,
.secondary-text {
  color: @text_muted;
}

.caption,
.tertiary-text {
  color: @text_dim;
  font-size: 12px;
}

.section-title {
  color: @text;
  font-size: 17px;
  font-weight: 700;
}

.detail-title {
  color: @text;
  font-size: 23px;
  font-weight: 800;
  letter-spacing: -0.35px;
}

.empty-state {
  color: @text_muted;
  font-size: 15px;
}

.app-sidebar {
  background-color: @sidebar;
  border-right: 1px solid @outline;
  padding: 20px 14px;
}

.app-sidebar-collapsed {
  padding: 16px 8px;
}

.sidebar-brand {
  padding: 0 10px 18px 10px;
}

.nav-caption {
  color: @text_dim;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 1px;
  padding: 22px 10px 8px 10px;
}

.sidebar-caption {
  color: @text_dim;
  font-size: 10px;
  font-weight: 800;
  padding: 22px 10px 8px 10px;
}

.sidebar-source {
  padding: 13px 12px;
  background-color: @surface;
  border: 1px solid @outline;
  border-radius: 13px;
}

.sidebar-source label {
  color: @text_muted;
  font-size: 12px;
  line-height: 1.35;
}

.sidebar-source label.source-badge {
  color: @accent;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.6px;
}

.sidebar-count {
  color: @text_dim;
  font-size: 11px;
  padding: 5px 10px 0 10px;
}

.workspace {
  padding: 16px 18px 18px 18px;
  background-color: @canvas;
}

.workspace-header {
  min-height: 64px;
  padding: 0 0 15px 0;
}

window.compact-height headerbar.topbar {
  min-height: 46px;
  padding: 4px 9px;
}

window.compact-height .workspace {
  padding: 8px 12px 10px 12px;
}

window.compact-height .workspace-header {
  min-height: 40px;
  padding-bottom: 5px;
}

window.compact-height .workspace-header button {
  min-height: 30px;
  padding: 4px 8px;
}

button {
  min-height: 34px;
  padding: 7px 12px;
  color: @text;
  background-color: @surface_raised;
  background-image: none;
  border: 1px solid @outline;
  border-radius: 10px;
  box-shadow: none;
  font-weight: 600;
  text-shadow: none;
}

button:hover {
  color: @text;
  background-color: @surface_hover;
  border-color: #456075;
}

button:active {
  background-color: #1b4249;
}

button:disabled {
  color: @text_dim;
  background-color: @surface;
  border-color: @outline;
  opacity: 0.55;
}

headerbar windowcontrols button,
headerbar button.titlebutton {
  min-width: 30px;
  min-height: 30px;
  padding: 3px;
  margin: 0 2px;
  color: @text_muted;
  background-color: transparent;
  background-image: none;
  border: 0;
  border-radius: 8px;
  box-shadow: none;
}

headerbar windowcontrols button:hover,
headerbar button.titlebutton:hover {
  color: @text;
  background-color: @surface_hover;
  border: 0;
}

headerbar windowcontrols button.close:hover,
headerbar button.titlebutton.close:hover {
  color: @danger;
  background-color: alpha(@danger, 0.14);
}

headerbar.topbar .window-actions {
  margin-right: 2px;
}

headerbar.topbar button.window-action {
  min-width: 34px;
  min-height: 34px;
  padding: 0;
  margin: 0;
  color: @text_muted;
  background-color: @surface_raised;
  border: 1px solid @outline;
  border-radius: 9px;
  box-shadow: none;
}

headerbar.topbar button.window-action:hover {
  color: @text;
  background-color: @surface_hover;
  border-color: #456075;
}

headerbar.topbar button.window-action:active {
  background-color: #1b4249;
}

headerbar.topbar button.window-action-close:hover {
  color: @danger;
  background-color: alpha(@danger, 0.14);
  border-color: alpha(@danger, 0.35);
}

button:focus-visible,
entry:focus-visible,
dropdown:focus-visible,
spinbutton:focus-visible,
switch:focus-visible,
list row:focus-visible {
  outline: 2px solid @accent;
  outline-offset: 2px;
}

button.suggested-action,
button.primary-action {
  color: @accent_ink;
  background-color: @accent;
  border-color: @accent;
  font-weight: 800;
}

button.suggested-action:hover,
button.primary-action:hover {
  color: @accent_ink;
  background-color: @accent_hover;
  border-color: @accent_hover;
}

button.suggested-action:disabled,
button.primary-action:disabled {
  color: @accent_ink;
  background-color: @accent;
  opacity: 0.43;
}

button.danger-action {
  color: @danger;
  background-color: alpha(@danger, 0.08);
  border-color: alpha(@danger, 0.3);
}

button.danger-action:hover {
  background-color: alpha(@danger, 0.18);
  border-color: @danger;
}

button.compact-button {
  min-width: 34px;
  min-height: 32px;
  padding: 5px 9px;
}

button.flat,
button.ghost-action {
  background-color: transparent;
  border-color: transparent;
}

button.flat:hover,
button.ghost-action:hover {
  background-color: @surface_hover;
}

button.nav-item {
  min-height: 45px;
  padding: 10px 13px;
  color: @text_muted;
  background-color: transparent;
  border: 1px solid transparent;
  border-radius: 11px;
  box-shadow: none;
  font-weight: 700;
}

button.nav-item:hover {
  color: @text;
  background-color: @surface_hover;
}

button.nav-item-active,
button.nav-item-active:hover {
  color: @accent;
  background-color: alpha(@accent, 0.10);
  border-color: alpha(@accent, 0.24);
}

button.language-choice {
  min-height: 56px;
  padding: 12px 16px;
  background-color: @surface;
  border: 1px solid @outline;
  border-radius: 12px;
  font-weight: 650;
}

button.language-choice:hover {
  background-color: @surface_hover;
}

button.language-choice-active,
button.language-choice-active:hover {
  color: @accent;
  background-color: alpha(@accent, 0.10);
  border-color: alpha(@accent, 0.48);
}

entry,
searchentry,
spinbutton,
dropdown {
  min-height: 38px;
  color: @text;
  background-color: @surface;
  background-image: none;
  border: 1px solid @outline;
  border-radius: 10px;
  box-shadow: none;
}

entry,
searchentry { padding: 5px 11px; }

window.compact-height .library-tools searchentry {
  min-height: 34px;
  padding: 2px 8px;
}

entry:focus,
searchentry:focus,
spinbutton:focus-within,
dropdown:focus {
  border-color: @accent;
  box-shadow: 0 0 0 2px alpha(@accent, 0.12);
}

entry placeholder { color: @text_dim; }

dropdown button,
spinbutton button {
  background-color: transparent;
  border: none;
  box-shadow: none;
}

switch {
  min-width: 46px;
  min-height: 26px;
  background-color: @outline;
  border: 1px solid #4a6071;
  border-radius: 999px;
}

switch:checked {
  background-color: @accent;
  border-color: @accent;
}

switch slider {
  min-width: 20px;
  min-height: 20px;
  background-color: @text;
  border-radius: 999px;
  box-shadow: 0 1px 5px alpha(@shadow, 0.35);
}

switch:checked slider { background-color: @accent_ink; }

scrolledwindow,
viewport,
flowbox,
list,
stack {
  background-color: transparent;
}

paned > separator {
  min-width: 1px;
  min-height: 1px;
  background-color: @outline;
  margin: 0 11px;
}

scrollbar slider {
  min-width: 5px;
  min-height: 5px;
  background-color: #526678;
  border-radius: 999px;
}

scrollbar slider:hover { background-color: #7d98aa; }

.library-tools {
  padding: 2px 0;
  background-color: transparent;
}

.surface,
.panel,
.inspector {
  background-color: @surface;
  border: 1px solid @outline;
  border-radius: 16px;
}

.panel-raised,
.stat-card {
  background-color: @surface_raised;
  border: 1px solid @outline;
  border-radius: 14px;
}

.panel,
.panel-raised,
.inspector { padding: 17px; }

.stat-card { padding: 14px 16px; }

.stat-value {
  color: @text;
  font-size: 22px;
  font-weight: 800;
}

.status-strip {
  min-height: 44px;
  padding: 7px 14px;
  color: @text_muted;
  background-color: @surface;
  border: 1px solid @outline;
  border-radius: 12px;
}

window.compact-height .status-strip {
  min-height: 32px;
  padding: 4px 8px;
}

.status-dot {
  color: @accent;
  font-size: 13px;
}

.status-dot.error,
.status-dot.offline { color: @danger; }

.status-indicator {
  color: @accent;
  font-size: 10px;
  font-weight: 800;
}

.status-strip.error,
.error {
  color: @danger;
  border-color: alpha(@danger, 0.32);
}

.hero {
  min-height: 182px;
  padding: 23px 25px;
  background-color: #19313c;
  background-image: linear-gradient(115deg, #173340, #22324e 64%, #302c52);
  border: 1px solid #35576b;
  border-radius: 19px;
}

.hero-title {
  color: #f7fbff;
  font-size: 25px;
  font-weight: 800;
}

.hero-copy { color: #d1e6ed; }

.hero-preview {
  background-color: #182330;
  border: 1px solid alpha(#ffffff, 0.16);
  border-radius: 13px;
  box-shadow: 0 12px 24px alpha(@shadow, 0.28);
}

.pill,
.badge {
  min-height: 20px;
  padding: 4px 9px;
  color: @text_muted;
  background-color: @surface_raised;
  border: 1px solid @outline;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
}

.pill-accent,
.badge-accent {
  color: @accent;
  background-color: alpha(@accent, 0.09);
  border-color: alpha(@accent, 0.30);
}

.filter-chip {
  min-height: 32px;
  padding: 6px 13px;
  color: @text_muted;
  background-color: @surface;
  border: 1px solid @outline;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
}

window.compact-height .filter-chip {
  min-height: 30px;
  padding: 3px 9px;
}

.filter-chip:hover {
  color: @text;
  background-color: @surface_hover;
  border-color: #4a6578;
}

.filter-chip-active,
.filter-chip-active:hover {
  color: @accent_ink;
  background-color: @accent;
  border-color: @accent;
  font-weight: 800;
}

.gallery-empty {
  min-height: 160px;
  padding: 26px;
  color: @text_muted;
  background-color: @surface;
  border: 1px dashed #42576b;
  border-radius: 15px;
  font-size: 15px;
  line-height: 1.5;
}

flowboxchild { padding: 0; }

flowboxchild.wallpaper-card {
  min-width: 205px;
  min-height: 166px;
  padding: 0;
  color: @text;
  background-color: @surface;
  border: 1px solid @outline;
  border-radius: 14px;
  box-shadow: 0 3px 10px alpha(@shadow, 0.14);
}

flowboxchild.wallpaper-card:hover {
  background-color: @surface_raised;
  border-color: #5d8e93;
  box-shadow: 0 8px 20px alpha(@shadow, 0.27);
}

flowboxchild.wallpaper-card:selected,
flowboxchild.wallpaper-card-selected,
flowboxchild.wallpaper-card-selected:hover {
  border: 2px solid @accent;
  background-color: @surface_raised;
  box-shadow: 0 0 0 2px alpha(@accent, 0.14);
}

.wallpaper-card picture,
.wallpaper-card image,
.artwork {
  background-color: #273443;
  border-radius: 12px 12px 0 0;
}

.wallpaper-card label {
  margin-left: 10px;
  margin-right: 10px;
}

.selection-bar {
  min-height: 50px;
  padding: 10px 13px;
  color: @text;
  background-color: #233548;
  border: 1px solid #4e7585;
  border-radius: 15px;
  box-shadow: 0 12px 28px alpha(@shadow, 0.48);
}

.selection-count {
  min-width: 108px;
  color: @accent;
  font-weight: 800;
}

.selection-bar.selection-bar-compact {
  min-height: 40px;
  padding: 6px 8px;
  border-radius: 12px;
}

.selection-bar-compact .selection-count { min-width: 0; }

.selection-bar-compact button {
  min-width: 34px;
  min-height: 34px;
  padding: 4px 7px;
}

.card-title {
  color: @text;
  font-size: 13px;
  font-weight: 700;
}

.card-meta {
  color: @text_dim;
  font-size: 11px;
}

.inspector {
  background-color: @sidebar;
  border-radius: 16px;
}

.inspector-header { padding-bottom: 5px; }
.inspector-actions { padding: 7px 0; }

.settings-row {
  min-height: 52px;
  padding: 13px 15px;
  background-color: @surface;
  border: 1px solid @outline;
  border-radius: 12px;
}

.settings-row:hover { background-color: @surface_raised; }

list row {
  min-height: 48px;
  margin: 3px 0;
  background-color: transparent;
  border: 1px solid transparent;
  border-radius: 10px;
}

list row:hover { background-color: @surface_hover; }

list row:selected {
  color: @accent;
  background-color: alpha(@accent, 0.10);
  border-color: alpha(@accent, 0.27);
}

list row:selected label { color: @text; }

.playlist-row { padding: 4px 7px; }

list row.playlist-item,
.playlist-item {
  min-height: 64px;
  margin: 4px 0;
  padding: 5px 8px;
  background-color: @surface;
  border: 1px solid @outline;
  border-radius: 12px;
}

list row.playlist-item:hover,
.playlist-item:hover {
  background-color: @surface_raised;
  border-color: #4a6578;
}

list row.playlist-item:selected,
.playlist-item:selected {
  background-color: alpha(@accent, 0.10);
  border-color: alpha(@accent, 0.52);
}

.playlist-entry {
  min-height: 48px;
  padding: 9px 12px;
  background-color: @surface;
  border: 1px solid @outline;
  border-radius: 11px;
}

.playlist-entry:hover { background-color: @surface_raised; }

.toast,
.notice {
  padding: 9px 13px;
  color: @text;
  background-color: @surface_raised;
  border: 1px solid @outline;
  border-radius: 10px;
}

.toast.error,
.notice.error {
  color: @danger;
  background-color: alpha(@danger, 0.08);
  border-color: alpha(@danger, 0.35);
}

dialog headerbar { background-color: @surface; }
dialog .dialog-action-area { padding: 12px; }
"""


def install_theme(display: Gdk.Display) -> Gtk.CssProvider:
    """Install the visual system at application priority for one display."""
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS.encode("utf-8"))
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    return provider
