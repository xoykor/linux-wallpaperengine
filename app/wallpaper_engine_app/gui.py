"""GTK4 control panel for the Linux Wallpaper Engine desktop service."""

from __future__ import annotations

from pathlib import Path
import subprocess
import threading
import time
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gtk, Pango

from . import ipc, model


SERVICE = "linux-wallpaperengine-app.service"
FILTERS = ("Todos", "Cenas", "Vídeos", "Favoritos")
SCALINGS = ("fill", "fit", "stretch", "default")

CSS = """
.wallpaper-card {
  padding: 0;
  border-radius: 12px;
  min-width: 206px;
  min-height: 168px;
}
.wallpaper-card image { border-radius: 9px 9px 0 0; }
.wallpaper-card label { margin: 3px 8px 0 8px; }
.detail-title { font-size: 1.45em; font-weight: 700; }
.status-strip { padding: 10px 14px; border-radius: 10px; background: alpha(@theme_fg_color, 0.07); }
.subtle { opacity: 0.72; }
.section-title { font-size: 1.1em; font-weight: 700; }
.settings-row { padding: 10px 14px; border-radius: 8px; background: alpha(@theme_fg_color, 0.05); }
.empty-state { font-size: 1.1em; opacity: 0.75; }
"""


def _label(text: str, *, css: str | None = None, wrap: bool = False) -> Gtk.Label:
    widget = Gtk.Label(label=text, xalign=0)
    widget.set_wrap(wrap)
    if css:
        widget.add_css_class(css)
    return widget


def _box(*, vertical: bool = False, spacing: int = 0) -> Gtk.Box:
    return Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL,
        spacing=spacing,
    )


def _clear(container: Gtk.Box | Gtk.FlowBox | Gtk.ListBox) -> None:
    child = container.get_first_child()
    while child is not None:
        following = child.get_next_sibling()
        container.remove(child)
        child = following


def _preview(path: str | None, width: int, height: int) -> Gtk.Widget:
    """Decode the first preview frame so gallery GIFs do not all animate."""
    if path and Path(path).is_file():
        try:
            animation = GdkPixbuf.PixbufAnimation.new_from_file(path)
            first = animation.get_static_image()
            scaled = first.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)
            if scaled is not None:
                picture = Gtk.Picture.new_for_pixbuf(scaled)
                # Gtk.ContentFit was added in GTK 4.8. Ubuntu 22.04 ships
                # GTK 4.6, so keep a compatible fallback instead of failing
                # while building the first preview card.
                if hasattr(Gtk, "ContentFit"):
                    picture.set_content_fit(Gtk.ContentFit.COVER)
                else:
                    picture.set_keep_aspect_ratio(True)
                picture.set_size_request(width, height)
                return picture
        except GLib.Error:
            pass
    icon = Gtk.Image.new_from_icon_name("image-x-generic-symbolic")
    icon.set_pixel_size(min(width, height) // 2)
    icon.set_size_request(width, height)
    return icon


class WallpaperWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application):
        super().__init__(application=app, title="Linux Wallpaper Engine")
        self.set_default_size(1200, 770)
        self.set_size_request(850, 570)

        self.catalog: dict[str, dict] = {}
        self.config: dict = {}
        self.status: dict = {}
        self.selected_id: str | None = None
        self.service_available = False
        self._updating_controls = False
        self._pending_status = False
        self._catalog_generation = 0
        self._cards: dict[str, Gtk.FlowBoxChild] = {}
        self._toast_timer = 0
        self.playlist_name: str | None = None
        self._updating_playlists = False
        self._catalog_scan_pending = False
        self._command_queue: list[tuple[str, dict[str, object]]] = []
        self._command_busy = False
        self._mutation_generation = 0

        self._build()
        self._load_catalog()
        self._refresh_status()
        self._refresh_autostart()
        GLib.timeout_add_seconds(2, self._tick)
        GLib.timeout_add_seconds(15, self._check_library_changes)

    def _build(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS.encode())
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        root = _box(vertical=True, spacing=8)
        root.set_margin_start(16)
        root.set_margin_end(16)
        root.set_margin_bottom(14)
        self.set_child(root)

        header = Gtk.HeaderBar()
        header.set_title_widget(_label("Linux Wallpaper Engine", css="section-title"))
        self.set_titlebar(header)

        self.tabs = Gtk.Stack()
        self.tabs.set_hexpand(True)
        self.tabs.set_vexpand(True)
        switcher = Gtk.StackSwitcher(stack=self.tabs)
        header.pack_start(switcher)

        self.reload_button = Gtk.Button(label="Atualizar biblioteca")
        self.reload_button.connect("clicked", lambda *_: self._reload())
        header.pack_end(self.reload_button)

        self.next_button = Gtk.Button(label="Próximo")
        self.next_button.connect("clicked", lambda *_: self._command("next"))
        header.pack_end(self.next_button)

        self.power_button = Gtk.Button(label="Iniciar")
        self.power_button.connect("clicked", self._toggle_power)
        header.pack_end(self.power_button)

        self.status_strip = _box(spacing=10)
        self.status_strip.add_css_class("status-strip")
        self.status_strip.set_valign(Gtk.Align.CENTER)
        self.status_label = _label("Verificando serviço…")
        self.status_label.set_hexpand(True)
        self.status_strip.append(self.status_label)
        self.countdown_label = _label("")
        self.countdown_label.add_css_class("subtle")
        self.status_strip.append(self.countdown_label)
        root.append(self.status_strip)

        self.tabs.add_titled(self._build_library(), "library", "Biblioteca")
        self.tabs.add_titled(self._build_playlists(), "playlists", "Playlists")
        self.tabs.add_titled(self._build_settings(), "settings", "Configurações")
        root.append(self.tabs)

        self.message = _label("")
        self.message.set_visible(False)
        self.message.set_wrap(True)
        root.append(self.message)

    def _build_library(self) -> Gtk.Widget:
        page = _box(vertical=True, spacing=12)
        page.set_margin_top(6)

        tools = _box(spacing=10)
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Buscar por título, tag ou ID")
        self.search.set_hexpand(True)
        self.search.connect("search-changed", lambda *_: self._filter_cards())
        tools.append(self.search)
        self.filter = Gtk.DropDown.new_from_strings(FILTERS)
        self.filter.connect("notify::selected", lambda *_: self._filter_cards())
        tools.append(self.filter)
        self.library_count = _label("Carregando…", css="subtle")
        tools.append(self.library_count)
        page.append(tools)

        panes = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        panes.set_wide_handle(True)
        panes.set_position(790)
        panes.set_hexpand(True)
        panes.set_vexpand(True)

        gallery_scroll = Gtk.ScrolledWindow()
        gallery_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.gallery = Gtk.FlowBox()
        self.gallery.set_valign(Gtk.Align.START)
        self.gallery.set_homogeneous(False)
        self.gallery.set_selection_mode(Gtk.SelectionMode.NONE)
        self.gallery.set_column_spacing(12)
        self.gallery.set_row_spacing(12)
        self.gallery.set_min_children_per_line(2)
        self.gallery.set_max_children_per_line(5)
        gallery_scroll.set_child(self.gallery)
        panes.set_start_child(gallery_scroll)

        detail_scroll = Gtk.ScrolledWindow()
        detail_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        detail_scroll.set_size_request(320, -1)
        self.detail = _box(vertical=True, spacing=12)
        self.detail.set_margin_start(16)
        self.detail.set_margin_end(5)
        self.detail.set_margin_bottom(12)
        detail_scroll.set_child(self.detail)
        panes.set_end_child(detail_scroll)
        self._show_details(None)
        page.append(panes)
        return page

    def _build_playlists(self) -> Gtk.Widget:
        page = _box(vertical=True, spacing=12)
        page.set_margin_top(8)

        toolbar = _box(spacing=10)
        heading = _label("Playlists", css="section-title")
        heading.set_hexpand(True)
        toolbar.append(heading)
        self.playlist_active_label = _label("Rotação pela biblioteca", css="subtle")
        toolbar.append(self.playlist_active_label)
        create = Gtk.Button(label="Criar playlist")
        create.connect("clicked", lambda *_: self._playlist_name_dialog("Criar playlist", None))
        toolbar.append(create)
        page.append(toolbar)

        panes = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        panes.set_wide_handle(True)
        panes.set_position(285)
        panes.set_hexpand(True)
        panes.set_vexpand(True)

        left_scroll = Gtk.ScrolledWindow()
        left_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.playlist_list = Gtk.ListBox()
        self.playlist_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.playlist_list.connect("row-selected", self._playlist_selected)
        left_scroll.set_child(self.playlist_list)
        panes.set_start_child(left_scroll)

        right_scroll = Gtk.ScrolledWindow()
        right_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.playlist_detail = _box(vertical=True, spacing=12)
        self.playlist_detail.set_margin_start(18)
        self.playlist_detail.set_margin_end(6)
        self.playlist_detail.set_margin_bottom(16)
        right_scroll.set_child(self.playlist_detail)
        panes.set_end_child(right_scroll)
        page.append(panes)
        self._refresh_playlists()
        return page

    def _refresh_playlists(self) -> None:
        if not hasattr(self, "playlist_list"):
            return
        playlists = self.config.get("playlists") or {}
        active = self.config.get("active_playlist")
        self.playlist_active_label.set_text(
            f"Ativa: {active}" if active else "Rotação pela biblioteca/favoritos"
        )
        if self.playlist_name not in playlists:
            self.playlist_name = next(iter(playlists), None)

        self._updating_playlists = True
        _clear(self.playlist_list)
        chosen_row = None
        for name, identifiers in playlists.items():
            row = Gtk.ListBoxRow()
            row.playlist_name = name
            content = _box(vertical=True, spacing=3)
            content.set_margin_top(10)
            content.set_margin_bottom(10)
            content.set_margin_start(12)
            content.set_margin_end(12)
            title = name + ("  ✓" if name == active else "")
            content.append(_label(title))
            content.append(_label(f"{len(identifiers)} wallpapers", css="subtle"))
            row.set_child(content)
            self.playlist_list.append(row)
            if name == self.playlist_name:
                chosen_row = row
        if chosen_row is not None:
            self.playlist_list.select_row(chosen_row)
        self._updating_playlists = False
        self._show_playlist_detail()

    def _playlist_selected(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if self._updating_playlists:
            return
        self.playlist_name = row.playlist_name if row is not None else None
        self._show_playlist_detail()

    def _show_playlist_detail(self) -> None:
        if not hasattr(self, "playlist_detail"):
            return
        _clear(self.playlist_detail)
        playlists = self.config.get("playlists") or {}
        name = self.playlist_name
        if name is None or name not in playlists:
            self.playlist_detail.append(
                _label("Crie uma playlist para escolher a ordem da rotação.",
                       css="empty-state", wrap=True)
            )
            return

        heading = _box(spacing=8)
        label = _label(name, css="detail-title")
        label.set_hexpand(True)
        heading.append(label)
        rename = Gtk.Button(label="Renomear")
        rename.connect("clicked", lambda *_: self._playlist_name_dialog("Renomear playlist", name))
        heading.append(rename)
        delete = Gtk.Button(label="Excluir")
        delete.connect("clicked", lambda *_: self._playlist_delete(name))
        heading.append(delete)
        self.playlist_detail.append(heading)

        active = self.config.get("active_playlist") == name
        activation = Gtk.Button(label="Usar biblioteca na rotação" if active else "Usar esta playlist na rotação")
        activation.set_sensitive(active or any(item_id in self.catalog for item_id in playlists[name]))
        if not active:
            activation.add_css_class("suggested-action")
        activation.connect(
            "clicked", lambda *_: self._set_settings(
                active_playlist=None if active else name,
                rotation_enabled=True,
            )
        )
        self.playlist_detail.append(activation)
        self.playlist_detail.append(_label(
            "A ordem abaixo vale quando a opção aleatória está desligada. "
            "Itens salvos que não estão instalados serão ignorados até reaparecerem na biblioteca.",
            css="subtle", wrap=True,
        ))

        available_ids = sorted(
            (wallpaper_id for wallpaper_id in self.catalog if wallpaper_id not in playlists[name]),
            key=lambda wallpaper_id: str(self.catalog[wallpaper_id].get("title", wallpaper_id)).casefold(),
        )
        if available_ids:
            add = _box(spacing=8)
            picker = Gtk.DropDown.new_from_strings([
                f"{self.catalog[wallpaper_id].get('title') or wallpaper_id} · {wallpaper_id}"
                for wallpaper_id in available_ids
            ])
            picker.set_hexpand(True)
            add.append(picker)
            add_button = Gtk.Button(label="Adicionar wallpaper instalado")
            add_button.connect(
                "clicked",
                lambda *_: self._playlist_add_id(
                    name,
                    available_ids[picker.get_selected()]
                    if picker.get_selected() < len(available_ids) else None,
                ),
            )
            add.append(add_button)
            self.playlist_detail.append(add)
        else:
            self.playlist_detail.append(_label(
                "Nenhum outro wallpaper instalado disponível. Atualize a biblioteca após assinar pelo Wallpaper Engine.",
                css="subtle", wrap=True,
            ))

        if self.selected_id and self.selected_id not in playlists[name]:
            selected_title = self.catalog.get(self.selected_id, {}).get("title", self.selected_id)
            add_selected = Gtk.Button(label=f"Adicionar selecionado: {selected_title}")
            add_selected.connect("clicked", lambda *_: self._playlist_add_id(name, self.selected_id))
            self.playlist_detail.append(add_selected)

        for position, wallpaper_id in enumerate(playlists[name]):
            item = self.catalog.get(wallpaper_id)
            title = item.get("title", wallpaper_id) if item else wallpaper_id
            if not item:
                title = f"{title} · não instalado"
            row = _box(spacing=7)
            row.add_css_class("settings-row")
            caption = _label(f"{position + 1}. {title}", wrap=True)
            caption.set_hexpand(True)
            row.append(caption)
            up = Gtk.Button(label="↑")
            up.set_sensitive(position > 0)
            up.set_tooltip_text("Mover para cima")
            up.connect("clicked", lambda _button, item_id=wallpaper_id: self._playlist_move(name, item_id, -1))
            row.append(up)
            down = Gtk.Button(label="↓")
            down.set_sensitive(position < len(playlists[name]) - 1)
            down.set_tooltip_text("Mover para baixo")
            down.connect("clicked", lambda _button, item_id=wallpaper_id: self._playlist_move(name, item_id, 1))
            row.append(down)
            remove = Gtk.Button(label="Remover")
            remove.connect("clicked", lambda _button, item_id=wallpaper_id: self._playlist_remove(name, item_id))
            row.append(remove)
            self.playlist_detail.append(row)

    def _playlist_name_dialog(self, title: str, previous: str | None) -> None:
        dialog = Gtk.Dialog(title=title, transient_for=self, modal=True)
        dialog.add_button("Cancelar", Gtk.ResponseType.CANCEL)
        dialog.add_button("Salvar", Gtk.ResponseType.ACCEPT)
        content = dialog.get_content_area()
        content.set_margin_top(15)
        content.set_margin_bottom(15)
        content.set_margin_start(15)
        content.set_margin_end(15)
        entry = Gtk.Entry()
        entry.set_placeholder_text("Nome da playlist")
        entry.set_text(previous or "")
        entry.set_activates_default(True)
        dialog.set_default_response(Gtk.ResponseType.ACCEPT)
        content.append(entry)

        def response(_dialog: Gtk.Dialog, code: int) -> None:
            if code == Gtk.ResponseType.ACCEPT:
                try:
                    name = model.normalize_playlist_name(entry.get_text())
                except ValueError as exc:
                    self._notice(str(exc), error=True)
                    return
                playlists = dict(self.config.get("playlists") or {})
                if name != previous and name.casefold() in {value.casefold() for value in playlists}:
                    self._notice("Já existe uma playlist com esse nome.", error=True)
                    return
                else:
                    if previous is not None and previous in playlists:
                        playlists = {
                            name if key == previous else key: value
                            for key, value in playlists.items()
                        }
                        active = self.config.get("active_playlist")
                        settings = {
                            "playlists": playlists,
                            "active_playlist": name if active == previous else active,
                        }
                    else:
                        playlists[name] = []
                        settings = {"playlists": playlists}
                    self.playlist_name = name
                    self._set_settings(**settings)
            dialog.destroy()

        dialog.connect("response", response)
        dialog.present()

    def _playlist_delete(self, name: str) -> None:
        if name not in (self.config.get("playlists") or {}):
            return
        dialog = Gtk.Dialog(title="Excluir playlist", transient_for=self, modal=True)
        dialog.add_button("Cancelar", Gtk.ResponseType.CANCEL)
        dialog.add_button("Excluir", Gtk.ResponseType.ACCEPT)
        content = dialog.get_content_area()
        content.set_margin_top(15)
        content.set_margin_bottom(15)
        content.set_margin_start(15)
        content.set_margin_end(15)
        content.append(_label(
            f"Excluir ‘{name}’? Os wallpapers instalados continuarão na biblioteca.",
            wrap=True,
        ))

        def response(_dialog: Gtk.Dialog, code: int) -> None:
            if code == Gtk.ResponseType.ACCEPT:
                playlists = dict(self.config.get("playlists") or {})
                if name in playlists:
                    del playlists[name]
                    active = self.config.get("active_playlist")
                    self.playlist_name = next(iter(playlists), None)
                    self._set_settings(
                        playlists=playlists,
                        active_playlist=None if active == name else active,
                    )
            dialog.destroy()

        dialog.connect("response", response)
        dialog.present()

    def _playlist_add_id(self, name: str, wallpaper_id: str | None) -> None:
        if wallpaper_id is None or wallpaper_id not in self.catalog:
            return
        playlists = {key: list(value) for key, value in (self.config.get("playlists") or {}).items()}
        if name not in playlists:
            return
        if wallpaper_id in playlists[name]:
            self._notice("Este wallpaper já está na playlist.")
            return
        playlists[name].append(wallpaper_id)
        self._set_settings(playlists=playlists)

    def _playlist_remove(self, name: str, wallpaper_id: str) -> None:
        playlists = {key: list(value) for key, value in (self.config.get("playlists") or {}).items()}
        if name not in playlists:
            return
        playlists[name] = [item for item in playlists[name] if item != wallpaper_id]
        self._set_settings(playlists=playlists)

    def _playlist_move(self, name: str, wallpaper_id: str, step: int) -> None:
        playlists = {key: list(value) for key, value in (self.config.get("playlists") or {}).items()}
        if name not in playlists or wallpaper_id not in playlists[name]:
            return
        index = playlists[name].index(wallpaper_id)
        other = index + step
        if 0 <= other < len(playlists[name]):
            playlists[name][index], playlists[name][other] = playlists[name][other], playlists[name][index]
            self._set_settings(playlists=playlists)

    def _build_settings(self) -> Gtk.Widget:
        scroll = Gtk.ScrolledWindow()
        body = _box(vertical=True, spacing=12)
        body.set_margin_top(16)
        body.set_margin_start(8)
        body.set_margin_end(8)
        body.set_margin_bottom(20)
        body.set_size_request(650, -1)
        body.set_halign(Gtk.Align.CENTER)
        body.set_hexpand(True)
        scroll.set_child(body)

        body.append(_label("Reprodução", css="section-title"))
        self.rotation_switch = self._switch_row(
            body, "Troca automática", "Escolhe outro wallpaper após o intervalo.",
            "rotation_enabled"
        )
        self.favorites_switch = self._switch_row(
            body, "Usar só favoritos", "Limita a rotação aos wallpapers marcados.",
            "only_favorites"
        )
        self.shuffle_switch = self._switch_row(
            body, "Ordem aleatória", "Embaralha os wallpapers elegíveis.", "shuffle"
        )
        self.interval_spin = self._spin_row(
            body, "Intervalo entre trocas", "Minutos", "interval_minutes", 1, 1440
        )

        body.append(_label("Renderização", css="section-title"))
        self.fps_spin = self._spin_row(body, "Limite de quadros", "FPS", "fps", 10, 240)
        self.scaling_drop = Gtk.DropDown.new_from_strings(
            ("Preencher", "Ajustar", "Esticar", "Padrão")
        )
        self.scaling_drop.connect("notify::selected", self._scaling_changed)
        self._widget_row(body, "Escala", "Como a imagem ocupa a tela.", self.scaling_drop)
        self.mute_switch = self._switch_row(
            body, "Silenciar", "Executa o renderizador sem áudio.", "mute"
        )

        body.append(_label("Aplicativo", css="section-title"))
        self.autostart_switch = Gtk.Switch()
        self.autostart_switch.connect("notify::active", self._autostart_changed)
        self._widget_row(
            body,
            "Iniciar com a sessão",
            "Ativa o serviço do usuário quando a sessão gráfica é iniciada.",
            self.autostart_switch,
        )
        self.renderer_entry = Gtk.Entry()
        self.renderer_entry.set_placeholder_text("Detectar linux-wallpaperengine no PATH")
        self.renderer_entry.set_hexpand(True)
        renderer_save = Gtk.Button(label="Salvar")
        renderer_save.connect(
            "clicked",
            lambda *_: self._set_settings(
                renderer_path=self.renderer_entry.get_text().strip() or "auto"
            ),
        )
        renderer_controls = _box(spacing=8)
        renderer_controls.append(self.renderer_entry)
        renderer_controls.append(renderer_save)
        self._widget_row(
            body, "Executável do motor", "Caminho personalizado, se necessário.", renderer_controls
        )

        info = _label(
            "Os wallpapers de cena e vídeo são lidos da biblioteca Steam instalada. "
            "A detecção de monitores usa KScreen quando disponível e XRandR em X11. "
            "O motor continua renderizando para preservar a estabilidade do vídeo "
            "e do áudio da sessão.",
            css="subtle",
            wrap=True,
        )
        info.set_margin_top(10)
        body.append(info)
        return scroll

    def _widget_row(self, parent: Gtk.Box, title: str, note: str, control: Gtk.Widget) -> None:
        row = _box(spacing=20)
        row.add_css_class("settings-row")
        text = _box(vertical=True, spacing=3)
        heading = _label(title)
        heading.set_hexpand(True)
        text.append(heading)
        text.append(_label(note, css="subtle", wrap=True))
        row.append(text)
        control.set_valign(Gtk.Align.CENTER)
        row.append(control)
        parent.append(row)

    def _switch_row(self, parent: Gtk.Box, title: str, note: str, key: str) -> Gtk.Switch:
        control = Gtk.Switch()
        control.connect("notify::active", self._switch_changed, key)
        self._widget_row(parent, title, note, control)
        return control

    def _spin_row(
        self, parent: Gtk.Box, title: str, unit: str, key: str, low: int, high: int
    ) -> Gtk.SpinButton:
        control = Gtk.SpinButton.new_with_range(low, high, 1)
        control.set_numeric(True)
        control.set_width_chars(5)
        control.connect("value-changed", self._spin_changed, key)
        self._widget_row(parent, title, unit, control)
        return control

    def _background(self, job: Callable, done: Callable) -> None:
        def worker() -> None:
            try:
                value = job()
                GLib.idle_add(done, value, None)
            except Exception as exc:
                GLib.idle_add(done, None, exc)

        threading.Thread(target=worker, daemon=True).start()

    def _load_catalog(self) -> None:
        self._catalog_generation += 1
        generation = self._catalog_generation
        self.library_count.set_text("Lendo biblioteca…")

        def done(value: dict | None, error: Exception | None) -> None:
            if generation != self._catalog_generation:
                return
            if error:
                self._notice(f"Erro ao ler wallpapers: {error}", error=True)
                self.library_count.set_text("Falha na leitura")
                return
            self.catalog = value or {}
            self.library_count.set_text(f"{len(self.catalog)} wallpapers")
            _clear(self.gallery)
            self._cards.clear()
            identifiers = sorted(
                self.catalog, key=lambda item: str(self.catalog[item].get("title", item)).casefold()
            )
            self._append_card_batch(identifiers, 0, generation)
            self._show_details(self.selected_id)
            self._refresh_playlists()

        self._background(model.scan_catalog, done)

    def _append_card_batch(self, ids: list[str], offset: int, generation: int) -> bool:
        if generation != self._catalog_generation:
            return False
        for wallpaper_id in ids[offset:offset + 12]:
            item = self.catalog[wallpaper_id]
            card = Gtk.Button()
            card.add_css_class("wallpaper-card")
            card.connect("clicked", lambda _button, chosen=wallpaper_id: self._show_details(chosen))
            content = _box(vertical=True, spacing=5)
            content.set_size_request(202, 162)
            content.append(_preview(item.get("preview"), 202, 113))
            title = _label(str(item.get("title") or wallpaper_id))
            title.set_ellipsize(Pango.EllipsizeMode.END)
            title.set_max_width_chars(25)
            content.append(title)
            kind = str(item.get("type", "")).capitalize()
            subtitle = _label(f"{kind} · {wallpaper_id}", css="subtle")
            subtitle.set_ellipsize(Pango.EllipsizeMode.END)
            content.append(subtitle)
            card.set_child(content)
            child = Gtk.FlowBoxChild()
            child.set_child(card)
            self.gallery.insert(child, -1)
            self._cards[wallpaper_id] = child
        self._filter_cards()
        if offset + 12 < len(ids):
            GLib.idle_add(self._append_card_batch, ids, offset + 12, generation)
        return False

    def _filter_cards(self) -> None:
        query = self.search.get_text().strip().casefold()
        mode = self.filter.get_selected()
        favorites = set(self.config.get("favorites", []))
        visible = 0
        for wallpaper_id, card in self._cards.items():
            item = self.catalog[wallpaper_id]
            haystack = " ".join(
                [str(item.get("title", "")), wallpaper_id]
                + [str(tag) for tag in item.get("tags", [])]
            ).casefold()
            matches = query in haystack
            if mode == 1:
                matches &= item.get("type") == "scene"
            elif mode == 2:
                matches &= item.get("type") == "video"
            elif mode == 3:
                matches &= wallpaper_id in favorites
            card.set_visible(matches)
            visible += bool(matches)
        self.library_count.set_text(f"{visible} de {len(self.catalog)}")

    def _show_details(self, wallpaper_id: str | None) -> None:
        self.selected_id = wallpaper_id if wallpaper_id in self.catalog else None
        _clear(self.detail)
        if self.selected_id is None:
            message = _label(
                "Escolha um wallpaper na biblioteca para ver a prévia e aplicar na tela.",
                css="empty-state",
                wrap=True,
            )
            message.set_margin_top(40)
            self.detail.append(message)
            return

        item = self.catalog[self.selected_id]
        self.detail.append(_preview(item.get("preview"), 290, 165))
        self.detail.append(_label(str(item.get("title") or self.selected_id), css="detail-title", wrap=True))
        self.detail.append(
            _label(f"{str(item.get('type', '')).capitalize()} · {self.selected_id}", css="subtle")
        )
        tags = item.get("tags", [])
        if tags:
            self.detail.append(_label(" · ".join(str(tag) for tag in tags[:8]), css="subtle", wrap=True))

        applied = Gtk.Button(label="Aplicar em todas as telas")
        applied.add_css_class("suggested-action")
        applied.connect("clicked", lambda *_: self._command("select", id=self.selected_id))
        self.detail.append(applied)

        favorite = self.selected_id in set(self.config.get("favorites", []))
        favorite_button = Gtk.Button(label="★ Remover dos favoritos" if favorite else "☆ Favoritar")
        favorite_button.connect("clicked", lambda *_: self._toggle_favorite(self.selected_id))
        self.detail.append(favorite_button)

        if self.playlist_name in (self.config.get("playlists") or {}):
            add_playlist = Gtk.Button(label=f"Adicionar à playlist: {self.playlist_name}")
            add_playlist.connect(
                "clicked", lambda *_: self._playlist_add_id(self.playlist_name, self.selected_id)
            )
            self.detail.append(add_playlist)

        screens = self.status.get("screens") or []
        assignments = self.config.get("screen_assignments", {})
        if screens:
            title = _label("Telas", css="section-title")
            title.set_margin_top(10)
            self.detail.append(title)
            for screen in screens:
                row = _box(spacing=6)
                row.set_valign(Gtk.Align.CENTER)
                name = _label(str(screen))
                name.set_hexpand(True)
                row.append(name)
                assigned = assignments.get(screen)
                if assigned == self.selected_id:
                    button = Gtk.Button(label="Liberar")
                    button.connect(
                        "clicked", lambda _button, target=screen: self._command(
                            "assign", screen=target, id=None
                        )
                    )
                else:
                    button = Gtk.Button(label="Fixar aqui")
                    button.connect(
                        "clicked", lambda _button, target=screen: self._command(
                            "assign", screen=target, id=self.selected_id
                        )
                    )
                row.append(button)
                self.detail.append(row)
                if assigned and assigned != self.selected_id:
                    other = self.catalog.get(assigned, {}).get("title", assigned)
                    self.detail.append(_label(f"Atual: {other}", css="subtle", wrap=True))

    def _toggle_favorite(self, wallpaper_id: str | None) -> None:
        if wallpaper_id is None:
            return
        favorites = set(self.config.get("favorites", []))
        if wallpaper_id in favorites:
            favorites.remove(wallpaper_id)
        else:
            favorites.add(wallpaper_id)
        self._set_settings(favorites=sorted(favorites))

    def _set_settings(self, **settings: object) -> None:
        try:
            self.config = model.validate_config(dict(self.config, **settings))
        except ValueError as exc:
            self._notice(str(exc), error=True)
            self._apply_config()
            return
        self._apply_config()
        if {"playlists", "active_playlist"} & settings.keys():
            self._refresh_playlists()
            self._show_details(self.selected_id)
        self._command("set", settings=settings)

    def _switch_changed(self, widget: Gtk.Switch, _property: object, key: str) -> None:
        if not self._updating_controls:
            self._set_settings(**{key: widget.get_active()})

    def _spin_changed(self, widget: Gtk.SpinButton, key: str) -> None:
        if not self._updating_controls:
            self._set_settings(**{key: widget.get_value_as_int()})

    def _scaling_changed(self, widget: Gtk.DropDown, _property: object) -> None:
        if not self._updating_controls:
            self._set_settings(scaling=SCALINGS[widget.get_selected()])

    def _apply_config(self) -> None:
        self._updating_controls = True
        try:
            for widget, key in (
                (self.rotation_switch, "rotation_enabled"),
                (self.favorites_switch, "only_favorites"),
                (self.shuffle_switch, "shuffle"),
                (self.mute_switch, "mute"),
            ):
                widget.set_active(bool(self.config.get(key, False)))
            self.favorites_switch.set_sensitive(self.config.get("active_playlist") is None)
            self.interval_spin.set_value(int(self.config.get("interval_minutes", 10)))
            self.fps_spin.set_value(int(self.config.get("fps", 30)))
            scaling = self.config.get("scaling", "fill")
            self.scaling_drop.set_selected(SCALINGS.index(scaling) if scaling in SCALINGS else 0)
            if not self.renderer_entry.has_focus():
                renderer = str(self.config.get("renderer_path") or "auto")
                self.renderer_entry.set_text("" if renderer == "auto" else renderer)
        finally:
            self._updating_controls = False
        self._filter_cards()

    def _command(self, command: str, **kwargs: object) -> None:
        self._mutation_generation += 1
        self._command_queue.append((command, kwargs))
        self._drain_commands()

    def _drain_commands(self) -> None:
        if self._command_busy or not self._command_queue:
            return
        command, kwargs = self._command_queue.pop(0)
        self._command_busy = True

        def done(value: dict | None, error: Exception | None) -> None:
            self._command_busy = False
            if error:
                self._notice(f"Não foi possível executar {command}: {error}", error=True)
                self._command_queue.clear()
                self._refresh_status()
                return
            if value:
                self._apply_status(value, accept_config=not self._command_queue)
            self._notice("Alteração aplicada.")
            self._drain_commands()

        self._background(lambda: ipc.request(command, **kwargs), done)

    def _refresh_status(self) -> None:
        if self._pending_status:
            return
        self._pending_status = True
        request_generation = self._mutation_generation
        had_pending_command = self._command_busy or bool(self._command_queue)

        def done(value: dict | None, error: Exception | None) -> None:
            self._pending_status = False
            if had_pending_command or request_generation != self._mutation_generation:
                return
            if error:
                if self.service_available:
                    self._notice(f"Serviço indisponível: {error}", error=True)
                self.service_available = False
                self.status_label.set_text("Serviço parado ou ainda não instalado")
                self.power_button.set_label("Iniciar")
                self.next_button.set_sensitive(False)
                self.countdown_label.set_text("")
                return
            self.service_available = True
            self._apply_status(value or {})

        self._background(lambda: ipc.request("status"), done)

    def _apply_status(self, status: dict, *, accept_config: bool = True) -> None:
        old_current = self.status.get("current_id")
        old_screens = self.status.get("screens")
        old_assignments = self.config.get("screen_assignments")
        old_favorites = self.config.get("favorites")
        old_playlists = self.config.get("playlists")
        old_active_playlist = self.config.get("active_playlist")
        self.status = status
        if accept_config:
            self.config = status.get("config") or self.config
        self._apply_config()
        running = bool(status.get("renderer_running"))
        current_id = status.get("current_id")
        if running and current_id:
            item = self.catalog.get(current_id, {})
            title = item.get("title") or current_id
            self.status_label.set_text(f"Em execução: {title}")
        elif status.get("error"):
            self.status_label.set_text(f"Aguardando: {status['error']}")
        elif status.get("running"):
            self.status_label.set_text("Iniciando wallpaper…")
        else:
            self.status_label.set_text("Serviço ativo · wallpaper parado")
        self.power_button.set_label("Parar" if status.get("running") else "Iniciar")
        screens = status.get("screens") or []
        assignments = self.config.get("screen_assignments", {})
        has_rotating_screen = any(screen not in assignments for screen in screens)
        self.next_button.set_sensitive(bool(status.get("running")) and has_rotating_screen)
        self._update_countdown()
        if (
            current_id != old_current
            or status.get("screens") != old_screens
            or self.config.get("screen_assignments") != old_assignments
            or self.config.get("favorites") != old_favorites
        ):
            self._show_details(self.selected_id)
        if (
            self.config.get("playlists") != old_playlists
            or self.config.get("active_playlist") != old_active_playlist
        ):
            self._refresh_playlists()
            self._show_details(self.selected_id)

    def _update_countdown(self) -> None:
        deadline = self.status.get("next_change_at")
        if not deadline or not self.config.get("rotation_enabled"):
            self.countdown_label.set_text("")
            return
        remaining = max(0, int(float(deadline) - time.time()))
        minutes, seconds = divmod(remaining, 60)
        self.countdown_label.set_text(f"Próxima troca em {minutes:02d}:{seconds:02d}")

    def _toggle_power(self, _button: Gtk.Button) -> None:
        if not self.service_available:
            self._systemctl("start")
        elif self.status.get("running"):
            self._command("stop")
        else:
            self._command("start")

    def _systemctl(self, verb: str) -> None:
        def run() -> None:
            result = subprocess.run(
                ["systemctl", "--user", verb, SERVICE],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode:
                raise RuntimeError(result.stderr.strip() or result.stdout.strip() or "systemctl falhou")

        def done(_value: object, error: Exception | None) -> None:
            if error:
                self._notice(f"Não foi possível {verb} o serviço: {error}", error=True)
            else:
                self._refresh_status()
                self._refresh_autostart()

        self._background(run, done)

    def _refresh_autostart(self) -> None:
        def run() -> bool:
            result = subprocess.run(
                ["systemctl", "--user", "is-enabled", SERVICE],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0 and result.stdout.strip() == "enabled"

        def done(value: bool | None, _error: Exception | None) -> None:
            self._updating_controls = True
            self.autostart_switch.set_active(bool(value))
            self._updating_controls = False

        self._background(run, done)

    def _autostart_changed(self, widget: Gtk.Switch, _property: object) -> None:
        if not self._updating_controls:
            self._systemctl("enable" if widget.get_active() else "disable")

    def _check_library_changes(self) -> bool:
        if not self.get_visible():
            return False
        if self._catalog_scan_pending:
            return True
        self._catalog_scan_pending = True

        def done(ids: set[str] | None, error: Exception | None) -> None:
            self._catalog_scan_pending = False
            if error:
                return
            if ids != set(self.catalog):
                self._load_catalog()

        self._background(lambda: set(model.scan_catalog()), done)
        return True

    def _reload(self) -> None:
        self._load_catalog()
        if self.service_available:
            self._command("reload")

    def _tick(self) -> bool:
        if not self.get_visible():
            return False
        self._update_countdown()
        self._refresh_status()
        return True

    def _notice(self, text: str, *, error: bool = False) -> None:
        self.message.set_text(text)
        self.message.set_visible(True)
        self.message.remove_css_class("error")
        if error:
            self.message.add_css_class("error")
        if self._toast_timer:
            GLib.source_remove(self._toast_timer)
        self._toast_timer = GLib.timeout_add_seconds(5, self._hide_notice)

    def _hide_notice(self) -> bool:
        self.message.set_visible(False)
        self._toast_timer = 0
        return False


class WallpaperApplication(Gtk.Application):
    def __init__(self):
        super().__init__(
            application_id="io.github.xoykor.LinuxWallpaperEngineApp",
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )

    def do_activate(self) -> None:
        window = self.props.active_window
        if window is None:
            window = WallpaperWindow(self)
        window.present()


def main() -> int:
    return WallpaperApplication().run(None)
