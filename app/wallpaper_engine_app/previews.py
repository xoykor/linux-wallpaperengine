"""Responsive, cached thumbnails for the GTK library and detail panel.

Only GdkPixbuf decoding runs off the GTK thread. Widgets and the cache are
created and updated on the main loop, so callers can use ``preview`` like any
other GTK widget factory.
"""

from __future__ import annotations

from collections import OrderedDict
from itertools import count
from pathlib import Path
from queue import PriorityQueue
import stat
from threading import Thread

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk


_PreviewKey = tuple[str, int, int]
_Signature = tuple[int, int]
_JobKey = tuple[_PreviewKey, _Signature]

_WORKERS = 3
_CACHE_ITEMS = 192
_CACHE_BYTES = 32 * 1024 * 1024

# The cache and pending widget lists are accessed only on GTK's main thread.
_cache: OrderedDict[_PreviewKey, tuple[_Signature, GdkPixbuf.Pixbuf | None, int]] = OrderedDict()
_cache_bytes = 0
_pending: dict[_JobKey, list[Gtk.Stack]] = {}
_tasks: PriorityQueue[tuple[int, int, _JobKey]] = PriorityQueue()
_sequence = count()
_workers_started = False


def _signature(path: str) -> _Signature | None:
    try:
        info = Path(path).stat()
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode):
        return None
    return info.st_mtime_ns, info.st_size


def _fit_pixbuf(frame: GdkPixbuf.Pixbuf, width: int, height: int) -> GdkPixbuf.Pixbuf | None:
    """Center-crop a frame to the card's preview ratio and scale it down."""
    source_width, source_height = frame.get_width(), frame.get_height()
    if source_width <= 0 or source_height <= 0:
        return None
    if source_width * height > source_height * width:
        crop_width = max(1, source_height * width // height)
        crop_height = source_height
    else:
        crop_width = source_width
        crop_height = max(1, source_width * height // width)
    left = (source_width - crop_width) // 2
    top = (source_height - crop_height) // 2
    cropped = GdkPixbuf.Pixbuf.new_subpixbuf(frame, left, top, crop_width, crop_height)
    return cropped.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)


def _decode(path: str, width: int, height: int) -> GdkPixbuf.Pixbuf | None:
    """Read the first frame, then center-crop without distorting its aspect."""
    try:
        frame = GdkPixbuf.PixbufAnimation.new_from_file(path).get_static_image()
        if frame is None:
            return None
        return _fit_pixbuf(frame, width, height)
    except Exception:
        # A missing or malformed Workshop preview should leave its placeholder.
        return None


def _cache_put(
    key: _PreviewKey, signature: _Signature, pixbuf: GdkPixbuf.Pixbuf | None
) -> None:
    global _cache_bytes
    previous = _cache.pop(key, None)
    if previous is not None:
        _cache_bytes -= previous[2]
    cost = pixbuf.get_rowstride() * pixbuf.get_height() if pixbuf else 0
    if cost > _CACHE_BYTES:
        return
    _cache[key] = (signature, pixbuf, cost)
    _cache_bytes += cost
    while len(_cache) > _CACHE_ITEMS or _cache_bytes > _CACHE_BYTES:
        _, (_, _, evicted_cost) = _cache.popitem(last=False)
        _cache_bytes -= evicted_cost


def _install(stack: Gtk.Stack, pixbuf: GdkPixbuf.Pixbuf | None) -> None:
    if pixbuf is None:
        return
    picture = Gtk.Picture.new_for_pixbuf(pixbuf)
    if hasattr(Gtk, "ContentFit"):
        picture.set_content_fit(Gtk.ContentFit.COVER)
    else:
        picture.set_keep_aspect_ratio(True)
    picture.set_hexpand(True)
    picture.set_vexpand(True)
    stack.add_named(picture, "preview")
    # A static thumbnail may finish decoding after the pointer has entered a
    # GIF preview. Keep the animation visible until its hover ends.
    visible = stack.get_visible_child_name()
    if visible != "animated-preview":
        stack.set_visible_child(picture)


def _deliver(job: _JobKey, pixbuf: GdkPixbuf.Pixbuf | None) -> bool:
    key, signature = job
    waiting = _pending.pop(job, [])
    # A Workshop update can replace a preview while a decode is in flight.
    if _signature(key[0]) != signature:
        return False
    _cache_put(key, signature, pixbuf)
    for stack in waiting:
        _install(stack, pixbuf)
    return False


def _worker() -> None:
    while True:
        _priority, _order, job = _tasks.get()
        try:
            key, _signature_value = job
            pixbuf = _decode(*key)
            GLib.idle_add(_deliver, job, pixbuf)
        finally:
            _tasks.task_done()


def _start_workers() -> None:
    global _workers_started
    if _workers_started:
        return
    _workers_started = True
    for index in range(_WORKERS):
        Thread(target=_worker, name=f"wallpaper-preview-{index}", daemon=True).start()


def preview(
    path: str | None,
    width: int,
    height: int,
    *,
    animation_path: str | None = None,
    hover_target: Gtk.Widget | None = None,
) -> Gtk.Widget:
    """Return a fixed-size widget that fills asynchronously with a first frame.

    Repeated requests for the same file and dimensions share decoding work and
    an LRU thumbnail. A changed file invalidates its previous cache entry.
    This function must be called on GTK's main thread.
    """
    width, height = max(1, int(width)), max(1, int(height))
    stack = Gtk.Stack()
    stack.add_css_class("wallpaper-preview")
    stack.set_size_request(width, height)
    stack.set_hexpand(False)
    stack.set_vexpand(False)
    # Leaving a GIF must detach its picture without an in-flight transition;
    # doing both at once can leave GTK drawing a removed child.
    stack.set_transition_type(Gtk.StackTransitionType.NONE)
    stack.set_transition_duration(0)
    placeholder = Gtk.Image.new_from_icon_name("image-x-generic-symbolic")
    placeholder.set_pixel_size(min(width, height) // 2)
    placeholder.add_css_class("dim-label")
    stack.add_named(placeholder, "placeholder")

    if not path:
        return stack
    signature = _signature(path)
    if signature is None:
        return stack

    def enable_gif_hover() -> None:
        gif_path = animation_path or (path if Path(path).suffix.casefold() == ".gif" else None)
        if not gif_path or Path(gif_path).suffix.casefold() != ".gif":
            return
        controller = Gtk.EventControllerMotion()
        # The preview sits below overlays (badges and controls). Capture the
        # pointer event at the preview surface so those children cannot hide
        # hover transitions.
        controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        state: dict[str, object] = {
            "animation": None,
            "iterator": None,
            "animated_picture": None,
            "ticker": 0,
            "last_frame": None,
            "loading": False,
            "hovering": False,
        }

        def start_animation(animation: GdkPixbuf.PixbufAnimation) -> None:
            if not state["hovering"]:
                return
            ticker = state.get("ticker")
            if isinstance(ticker, int) and ticker:
                GLib.source_remove(ticker)
                state["ticker"] = 0
            picture = Gtk.Picture()
            picture.set_size_request(width, height)
            picture.set_hexpand(True)
            picture.set_vexpand(True)
            picture.set_can_shrink(True)
            if hasattr(Gtk, "ContentFit"):
                picture.set_content_fit(Gtk.ContentFit.COVER)
            else:
                picture.set_keep_aspect_ratio(True)
            iterator = animation.get_iter(None)
            state["iterator"] = iterator
            state["animated_picture"] = picture
            stack.add_named(picture, "animated-preview")
            stack.set_visible_child(picture)

            def advance_frame() -> bool:
                state["ticker"] = 0
                if not state["hovering"]:
                    return False
                current = state["iterator"]
                target = state["animated_picture"]
                if current is None or target is None:
                    return False
                changed = current.advance(None)
                frame = current.get_pixbuf()
                # GdkPixbuf may return the same Pixbuf wrapper while mutating
                # its pixels for the next GIF frame. Use the iterator's
                # changed flag instead of Python object identity.
                if changed or state["last_frame"] is None:
                    fitted = _fit_pixbuf(frame, width, height)
                    if fitted is not None:
                        texture = Gdk.Texture.new_for_pixbuf(fitted)
                        target.set_paintable(texture)
                    state["last_frame"] = frame
                # Follow the GIF's frame delay instead of waking GTK on a
                # fixed 40 ms loop for animations with fewer frames.
                delay = max(20, int(current.get_delay_time()))
                state["ticker"] = GLib.timeout_add(delay, advance_frame)
                return False

            advance_frame()

        def deliver_animation(animation: GdkPixbuf.PixbufAnimation | None) -> bool:
            state["loading"] = False
            if animation is None or animation.is_static_image():
                return False
            state["animation"] = animation
            start_animation(animation)
            return False

        def load_animation() -> None:
            try:
                animation = GdkPixbuf.PixbufAnimation.new_from_file(gif_path)
            except Exception:
                animation = None
            GLib.idle_add(deliver_animation, animation)

        def enter(_controller: Gtk.EventControllerMotion, _x: float, _y: float) -> None:
            state["hovering"] = True
            animation = state["animation"]
            if isinstance(animation, GdkPixbuf.PixbufAnimation):
                start_animation(animation)
                return
            if state["loading"]:
                return
            state["loading"] = True
            Thread(target=load_animation, name="wallpaper-gif-preview", daemon=True).start()

        def leave(_controller: Gtk.EventControllerMotion) -> None:
            state["hovering"] = False
            ticker = state.get("ticker")
            if isinstance(ticker, int) and ticker:
                GLib.source_remove(ticker)
                state["ticker"] = 0
            if stack.get_child_by_name("preview") is not None:
                stack.set_visible_child_name("preview")
            else:
                stack.set_visible_child_name("placeholder")
            animated_picture = state.get("animated_picture")
            if isinstance(animated_picture, Gtk.Widget):
                stack.remove(animated_picture)
            state["animation"] = None
            state["iterator"] = None
            state["animated_picture"] = None
            state["last_frame"] = None

        controller.connect("enter", enter)
        controller.connect("leave", leave)
        (hover_target if hover_target is not None else stack).add_controller(controller)

    # Attach hover handling before cache/pending early returns, so duplicate
    # requests for the same preview behave exactly like the first one.
    enable_gif_hover()

    key = (path, width, height)
    cached = _cache.get(key)
    if cached is not None and cached[0] == signature:
        _cache.move_to_end(key)
        _install(stack, cached[1])
        return stack

    job = (key, signature)
    if job in _pending:
        _pending[job].append(stack)
        return stack
    _pending[job] = [stack]
    _start_workers()
    # Detail previews jump ahead of a large gallery's remaining thumbnails.
    priority = 0 if width >= 250 else 1
    _tasks.put((priority, next(_sequence), job))
    return stack
