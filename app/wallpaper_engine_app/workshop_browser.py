"""Steam Workshop browser, isolated from the main GTK 4 process.

Run with ``python3 -m wallpaper_engine_app.workshop_browser``. Selecting an
item writes exactly ``ITEM <id>`` to stdout for the caller; diagnostics go to
stderr. Steam authentication and subscriptions happen on Steam's own site.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import sys
from urllib.parse import parse_qs, urlencode, urlsplit


WORKSHOP_URL = "https://steamcommunity.com/workshop/browse/?appid=431960"
_ITEM_ID = re.compile(r"[0-9]{1,24}\Z")
_ITEM_PATHS = {"/sharedfiles/filedetails", "/workshop/filedetails"}
_STEAM_HOSTS = ("steamcommunity.com", "steampowered.com")


def browse_url(search: str = "") -> str:
    """Build an official Workshop URL without interpolating raw user input."""
    query = search.strip()
    if not query:
        return WORKSHOP_URL
    return "https://steamcommunity.com/workshop/browse/?" + urlencode(
        {"appid": "431960", "searchtext": query}
    )


def item_url(item_id: str) -> str:
    if not _ITEM_ID.fullmatch(item_id):
        raise ValueError("O ID do Workshop deve ser numérico.")
    return "https://steamcommunity.com/sharedfiles/filedetails/?id=" + item_id


def is_steam_page(uri: str | None) -> bool:
    if not uri:
        return False
    parts = urlsplit(uri)
    if parts.scheme != "https":
        return False
    host = (parts.hostname or "").lower()
    return any(host == domain or host.endswith("." + domain) for domain in _STEAM_HOSTS)


def workshop_id(uri: str | None) -> str | None:
    """Only accept a numeric ID from an official Steam item detail URL."""
    if not is_steam_page(uri):
        return None
    parts = urlsplit(uri or "")
    if (parts.hostname or "").lower() not in {"steamcommunity.com", "www.steamcommunity.com"}:
        return None
    if parts.path.rstrip("/") not in _ITEM_PATHS:
        return None
    ids = parse_qs(parts.query).get("id", [])
    if len(ids) != 1 or not _ITEM_ID.fullmatch(ids[0]):
        return None
    return ids[0]


def profile_directory() -> Path:
    raw = os.environ.get("XDG_DATA_HOME")
    base = Path(raw).expanduser() if raw and Path(raw).expanduser().is_absolute() else Path.home() / ".local/share"
    return base / "linux-wallpaperengine-app/webkit"


class WorkshopBrowser:
    """A self-contained GTK 3 window hosting the official Steam web portal."""

    def __init__(
        self, Gtk, WebKit2, Gio, *, search: str = "", item: str | None = None,
        load_initial: bool = True,
    ):
        self.Gtk = Gtk
        self.WebKit2 = WebKit2
        self.Gio = Gio
        self.search_text = search
        self._failed_load = False
        self._popups = []

        profile = profile_directory()
        profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        profile.chmod(0o700)
        manager = WebKit2.WebsiteDataManager(
            base_data_directory=str(profile / "data"),
            base_cache_directory=str(profile / "cache"),
        )
        self.context = WebKit2.WebContext.new_with_website_data_manager(manager)
        self.context.get_cookie_manager().set_persistent_storage(
            str(profile / "cookies.sqlite"), WebKit2.CookiePersistentStorage.SQLITE
        )

        self.window = Gtk.Window(title="Steam Workshop · Wallpaper Engine")
        self.window.set_default_size(1200, 800)
        self.window.connect("destroy", self._window_destroyed)

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        column.set_border_width(8)
        self.window.add(column)

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        column.pack_start(toolbar, False, False, 0)
        self.back_button = Gtk.Button.new_with_label("Voltar")
        self.back_button.connect("clicked", lambda *_: self.webview.go_back())
        toolbar.pack_start(self.back_button, False, False, 0)
        self.forward_button = Gtk.Button.new_with_label("Avançar")
        self.forward_button.connect("clicked", lambda *_: self.webview.go_forward())
        toolbar.pack_start(self.forward_button, False, False, 0)
        refresh_button = Gtk.Button.new_with_label("Recarregar")
        refresh_button.connect("clicked", lambda *_: self.webview.reload())
        toolbar.pack_start(refresh_button, False, False, 0)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Buscar no Workshop")
        self.search_entry.set_text(search)
        self.search_entry.connect("activate", self._search)
        toolbar.pack_start(self.search_entry, True, True, 0)
        search_button = Gtk.Button.new_with_label("Buscar")
        search_button.connect("clicked", self._search)
        toolbar.pack_start(search_button, False, False, 0)

        self.webview = WebKit2.WebView.new_with_context(self.context)
        self.webview.connect("load-changed", self._load_changed)
        self.webview.connect("load-failed", self._load_failed)
        self.webview.connect("decide-policy", self._decide_policy)
        self.webview.connect("create", self._create_popup)
        self.webview.connect("web-process-terminated", self._web_process_terminated)
        self.webview.connect("notify::uri", self._uri_changed)
        column.pack_start(self.webview, True, True, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        column.pack_start(actions, False, False, 0)
        self.steam_button = Gtk.Button.new_with_label("Abrir no Steam")
        self.steam_button.connect("clicked", self._open_item_in_steam)
        actions.pack_start(self.steam_button, False, False, 0)
        self.use_button = Gtk.Button.new_with_label("Usar no app")
        self.use_button.connect("clicked", self._use_item)
        actions.pack_start(self.use_button, False, False, 0)
        browser_button = Gtk.Button.new_with_label("Abrir Workshop no navegador")
        browser_button.connect("clicked", self._open_workshop_externally)
        actions.pack_end(browser_button, False, False, 0)

        self.help_label = Gtk.Label(
            label="Assine no site oficial nesta janela. O cliente Steam faz o download. "
            "Se o login ou a página não funcionar aqui, abra o Workshop no navegador."
        )
        self.help_label.set_xalign(0)
        self.help_label.set_line_wrap(True)
        column.pack_start(self.help_label, False, False, 0)

        self.status = Gtk.Label()
        self.status.set_xalign(0)
        self.status.set_line_wrap(True)
        column.pack_start(self.status, False, False, 0)
        self._update_navigation()
        if load_initial:
            self.webview.load_uri(item_url(item) if item else browse_url(search))

    def _set_status(self, message: str) -> None:
        self.status.set_text(message)

    def _window_destroyed(self, *_args) -> None:
        if self.Gtk.main_level() > 0:
            self.Gtk.main_quit()

    def _update_navigation(self) -> None:
        self.back_button.set_sensitive(self.webview.can_go_back())
        self.forward_button.set_sensitive(self.webview.can_go_forward())
        item_id = workshop_id(self.webview.get_uri())
        self.steam_button.set_sensitive(item_id is not None)
        self.use_button.set_sensitive(item_id is not None)

    def _search(self, *_args) -> None:
        self.search_text = self.search_entry.get_text()
        self.webview.load_uri(browse_url(self.search_text))

    def _uri_changed(self, *_args) -> None:
        self._update_navigation()

    def _load_changed(self, _view, event) -> None:
        self._update_navigation()
        if event == self.WebKit2.LoadEvent.FINISHED:
            # WebKit emits FINISHED after load-failed, potentially with the
            # previous page's successful response still attached.
            if self._failed_load:
                self._failed_load = False
                return
            resource = self.webview.get_main_resource()
            response = resource.get_response() if resource else None
            if response and response.get_status_code() >= 400:
                self._set_status(
                    "O Steam recusou esta página. Tente recarregar ou use "
                    "‘Abrir Workshop no navegador’ para entrar e assinar por lá."
                )
            elif response and 200 <= response.get_status_code() < 400 and is_steam_page(self.webview.get_uri()):
                self._set_status("")

    def _load_failed(self, _view, _event, _uri, _error) -> bool:
        # Do not print failing URLs: login redirects can contain session data.
        if _view is self.webview:
            self._failed_load = True
        self._set_status(
            "Não foi possível carregar o Steam nesta janela. Verifique a conexão "
            "ou abra o Workshop no navegador padrão. O login pode ser incompatível "
            "com esta versão do WebKit."
        )
        return True

    def _web_process_terminated(self, _view, _reason) -> None:
        self._set_status(
            "O navegador integrado parou de funcionar. Abra o Workshop no "
            "navegador padrão e tente novamente."
        )

    def _launch_external(self, uri: str) -> bool:
        try:
            self.Gio.AppInfo.launch_default_for_uri(uri, None)
        except Exception:
            self._set_status("Não foi possível abrir o link no aplicativo padrão.")
            return False
        return True

    def _create_popup(self, source_view, navigation_action):
        """Give Steam authentication popups a related view and shared cookies."""
        uri = navigation_action.get_request().get_uri() or ""
        if uri and not is_steam_page(uri) and not uri.startswith("about:blank"):
            if urlsplit(uri).scheme in {"http", "https", "steam", "mailto"}:
                self._launch_external(uri)
            else:
                self._set_status("Este tipo de link foi bloqueado no navegador integrado.")
            return None
        # Steam can create about:blank and assign its URL with JavaScript.
        # A related view preserves window.opener and the same WebKit profile.
        popup_view = self.WebKit2.WebView.new_with_related_view(source_view)
        popup_window = self.Gtk.Window(title="Steam · login")
        popup_window.set_default_size(850, 700)
        popup_window.set_transient_for(self.window)
        popup_window.set_destroy_with_parent(True)
        popup_column = self.Gtk.Box(orientation=self.Gtk.Orientation.VERTICAL, spacing=6)
        popup_column.set_border_width(6)
        popup_window.add(popup_column)
        popup_column.pack_start(popup_view, True, True, 0)
        popup_notice = self.Gtk.Label()
        popup_notice.set_xalign(0)
        popup_notice.set_line_wrap(True)
        popup_column.pack_start(popup_notice, False, False, 0)
        popup_view.connect("decide-policy", self._decide_policy)
        popup_view.connect("create", self._create_popup)
        popup_view.connect("ready-to-show", lambda *_: popup_window.show_all())
        popup_view.connect("close", lambda *_: popup_window.destroy())
        def popup_failed(_view, _event, _uri, _error):
            popup_notice.set_text(
                "A página de login não carregou. Feche esta janela e abra o "
                "Workshop no navegador padrão."
            )
            return True
        popup_view.connect("load-failed", popup_failed)
        popup_window.connect("destroy", lambda *_: self._popups.remove(popup_window) if popup_window in self._popups else None)
        self._popups.append(popup_window)
        return popup_view

    def _decide_policy(self, _view, decision, decision_type) -> bool:
        webkit = self.WebKit2
        if decision_type not in (
            webkit.PolicyDecisionType.NAVIGATION_ACTION,
            webkit.PolicyDecisionType.NEW_WINDOW_ACTION,
        ):
            return False
        uri = decision.get_request().get_uri()
        parts = urlsplit(uri or "")
        if is_steam_page(uri):
            # Let NEW_WINDOW_ACTION reach WebView::create so login popups keep
            # window.opener and use the same Steam authentication context.
            return False
        if uri == "about:blank":
            return False
        if parts.scheme in {"javascript", "blob"} and is_steam_page(_view.get_uri()):
            return False
        decision.ignore()
        if parts.scheme in {"http", "https", "steam"}:
            self._launch_external(uri)
        else:
            self._set_status("Este tipo de link foi bloqueado no navegador integrado.")
        return True

    def _open_item_in_steam(self, *_args) -> None:
        item_id = workshop_id(self.webview.get_uri())
        if item_id:
            self._launch_external(f"steam://url/CommunityFilePage/{item_id}")

    def _open_workshop_externally(self, *_args) -> None:
        self._launch_external(browse_url(self.search_text))

    def _use_item(self, *_args) -> None:
        item_id = workshop_id(self.webview.get_uri())
        if not item_id:
            return
        print(f"ITEM {item_id}", flush=True)
        self.Gtk.main_quit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Browse Wallpaper Engine's Steam Workshop")
    start = parser.add_mutually_exclusive_group()
    start.add_argument("--search", default="", help="Initial Workshop search text")
    start.add_argument("--item", help="Open a numeric Workshop item ID")
    args = parser.parse_args(argv)
    if args.item and not _ITEM_ID.fullmatch(args.item):
        parser.error("--item requer um ID numérico do Workshop")
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("WebKit2", "4.1")
        from gi.repository import Gio, Gtk, WebKit2
    except (ImportError, ValueError):
        print(
            "Navegador do Workshop indisponível: instale GTK 3, WebKit2GTK 4.1 "
            "e as introspecções Python gi.",
            file=sys.stderr,
        )
        return 2

    ready, _remaining = Gtk.init_check([])
    if not ready:
        print("Navegador do Workshop requer uma sessão gráfica GTK.", file=sys.stderr)
        return 2
    # The WebKit profile contains cookies. Keep all newly-created files private.
    os.umask(0o077)
    try:
        browser = WorkshopBrowser(Gtk, WebKit2, Gio, search=args.search, item=args.item)
    except Exception:
        print(
            "Não foi possível iniciar o navegador integrado do Workshop. "
            "Abra o Workshop no navegador padrão.",
            file=sys.stderr,
        )
        return 2
    browser.window.show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
