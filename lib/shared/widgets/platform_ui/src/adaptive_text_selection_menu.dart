import 'dart:async';

import 'package:flutter/services.dart';
import 'package:material_ui/material_ui.dart';

/// Uses UIKit's edit menu for read-only iOS selections, retaining Flutter's
/// selection/actions and its standard toolbar on other or unavailable hosts.
class AdaptiveTextSelectionMenu extends StatefulWidget {
  const AdaptiveTextSelectionMenu({
    super.key,
    required this.selectionRect,
    required this.anchors,
    required this.buttonItems,
    required this.onDismiss,
  });

  final Rect selectionRect;
  final TextSelectionToolbarAnchors anchors;
  final List<ContextMenuButtonItem> buttonItems;
  final VoidCallback onDismiss;

  @override
  State<AdaptiveTextSelectionMenu> createState() =>
      _AdaptiveTextSelectionMenuState();
}

class _AdaptiveTextSelectionMenuState extends State<AdaptiveTextSelectionMenu> {
  static const _channel = MethodChannel(
    'app.cogwheel.conduit/text_selection_menu',
  );
  static int _nextSession = 0;
  int? _session;
  bool _fallback = false;
  bool _scheduled = false;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _schedulePresentation();
  }

  @override
  void didUpdateWidget(AdaptiveTextSelectionMenu oldWidget) {
    super.didUpdateWidget(oldWidget);
    _schedulePresentation();
  }

  void _schedulePresentation() {
    if (_fallback || _scheduled || !SystemContextMenu.isSupported(context)) {
      return;
    }
    _scheduled = true;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _scheduled = false;
      if (mounted) unawaited(_show());
    });
  }

  /// Freezes this presentation's callbacks and fences replies against a newer
  /// selection, so a dismissed menu cannot act on a replacement selection.
  Future<void> _show() async {
    final session = _nextSession++;
    final items = List<ContextMenuButtonItem>.of(widget.buttonItems);
    final view = View.of(context);
    final size = view.physicalSize / view.devicePixelRatio;
    final rect = widget.selectionRect;
    _session = session;
    try {
      final action = await _channel.invokeMethod<int>('show', {
        'session': session,
        'rect': [rect.left, rect.top, rect.width, rect.height],
        'viewWidth': size.width,
        'viewHeight': size.height,
        'dark': Theme.of(context).brightness == Brightness.dark,
        'labels': [
          for (final item in items)
            AdaptiveTextSelectionToolbar.getButtonLabel(context, item),
        ],
      });
      if (!mounted || _session != session) return;
      if (action != null && action >= 0 && action < items.length) {
        items[action].onPressed?.call();
      } else {
        widget.onDismiss();
      }
    } on MissingPluginException {
      _useFallback(session);
    } on PlatformException {
      _useFallback(session);
    }
  }

  void _useFallback(int session) {
    if (mounted && _session == session) setState(() => _fallback = true);
  }

  /// The native bridge ignores a hide belonging to an older menu.
  Future<void> _hide(int session) async {
    try {
      await _channel.invokeMethod<void>('hide', {'session': session});
    } on MissingPluginException {
      // An unavailable host is already using the Flutter toolbar.
    } on PlatformException {
      // Engine teardown can race selection disposal.
    }
  }

  @override
  void dispose() {
    final session = _session;
    if (session != null) unawaited(_hide(session));
    super.dispose();
  }

  @override
  Widget build(BuildContext context) =>
      _fallback || !SystemContextMenu.isSupported(context)
      ? AdaptiveTextSelectionToolbar.buttonItems(
          anchors: widget.anchors,
          buttonItems: widget.buttonItems,
        )
      : const SizedBox.shrink();
}
