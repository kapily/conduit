import 'package:flutter/rendering.dart' show ClearSelectionEvent;
import 'package:material_ui/material_ui.dart';

/// Region count above which settled markdown regions (root blocks or display
/// parts) inflate across frames instead of in one.
const int markdownChunkedPartInflationThreshold = 128;

/// A column of markdown block widgets that inflates in bounded batches.
///
/// Attribution profiling of the multi-second freeze when opening a
/// conversation with a very large settled message showed the time was NOT
/// markdown parsing (moved off-thread earlier) but element inflation + first
/// layout of thousands of block widgets in a single frame (~6–8 s for ~2.4k
/// blocks; a same-size single block laid out in ~150 ms). Splitting the
/// mount across frames keeps every frame bounded while the transcript's
/// initial-position settlement (which is already frame-polled) tracks the
/// growing height.
///
/// The widgets are prebuilt by the caller — building them is cheap; only
/// their inflation is chunked. Existing children keep their render objects
/// as subsequent batches are appended.
class ChunkedBlockColumn extends StatefulWidget {
  const ChunkedBlockColumn({
    required this.children,
    this.revealImmediately = false,
    this.initialChunk = 48,
    this.chunkSize = 80,
    super.key,
  });

  /// Prebuilt block widgets in document order. The list may be a fresh
  /// object on every ancestor rebuild; reveal progress is keyed on count,
  /// not list identity.
  final List<Widget> children;

  /// While true, every child is revealed synchronously — no chunking. Used
  /// during streaming, where blocks arrive one at a time (so no single frame
  /// inflates a large batch) and the live tail must never trail a frame
  /// behind the pin/extent math. Progress is monotonic: children revealed
  /// under this mode stay revealed when it turns off, so the
  /// streaming→settled flip never re-inflates or collapses the body.
  final bool revealImmediately;

  /// Blocks inflated synchronously on the first frame — enough to overfill
  /// a phone viewport so the reveal is invisible at the reading edge.
  final int initialChunk;

  /// Blocks appended per subsequent frame.
  final int chunkSize;

  @override
  State<ChunkedBlockColumn> createState() => _ChunkedBlockColumnState();
}

class _ChunkedBlockColumnState extends State<ChunkedBlockColumn> {
  late List<Widget> _children;
  late int _revealed;
  bool _revealScheduled = false;

  @override
  void initState() {
    super.initState();
    _cacheChildren();
    _revealed =
        widget.revealImmediately ||
            widget.children.length <= widget.initialChunk
        ? widget.children.length
        : widget.initialChunk;
    _scheduleRevealIfNeeded();
  }

  @override
  void didUpdateWidget(covariant ChunkedBlockColumn oldWidget) {
    super.didUpdateWidget(oldWidget);
    _cacheChildren();
    // Content updates never reset progress: a message that grows (the
    // responseDone gap keeps appending after isStreaming flips false) keeps
    // everything already revealed and streams the appended blocks in;
    // resetting to the initial chunk would visibly collapse the body.
    // Shrinking documents clamp.
    if (widget.revealImmediately) {
      _revealed = widget.children.length;
    } else if (_revealed > widget.children.length) {
      _revealed = widget.children.length;
    }
    _scheduleRevealIfNeeded();
  }

  void _cacheChildren() {
    // An edit can move a keyed block across group boundaries. Keep its subtree
    // alive across that move, and reuse these wrappers during reveal frames.
    _children = widget.children
        .map((child) {
          final key = child.key;
          if (key is! LocalKey) return child;
          return KeyedSubtree(
            key: MarkdownBlockKey(this, key),
            // Replace plain identity wrappers instead of adding another element.
            child: child.runtimeType == KeyedSubtree
                ? (child as KeyedSubtree).child
                : child,
          );
        })
        .toList(growable: false);
  }

  bool get _isComplete => _revealed >= widget.children.length;

  void _scheduleRevealIfNeeded() {
    if (widget.revealImmediately || _isComplete || _revealScheduled) return;
    _revealScheduled = true;
    final binding = WidgetsBinding.instance;
    binding.addPostFrameCallback((_) {
      _revealScheduled = false;
      if (!mounted || _isComplete) return;
      setState(() {
        _revealed = (_revealed + widget.chunkSize).clamp(
          0,
          widget.children.length,
        );
      });
      _scheduleRevealIfNeeded();
    });
    binding.scheduleFrame();
  }

  @override
  Widget build(BuildContext context) {
    const groupSize = 64;
    // Keep short replies directly selectable, without nested registration.
    // Fixed groups bound selection merging and layout without reparenting
    // children when the reveal batch size changes.
    final children = _revealed <= groupSize
        ? (_isComplete ? _children : _children.sublist(0, _revealed))
        : <Widget>[
            ..._children.take(groupSize),
            for (var start = groupSize; start < _revealed; start += groupSize)
              _BlockGroup(
                children: _children.sublist(
                  start,
                  (start + groupSize).clamp(0, _revealed),
                ),
              ),
          ];
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: children,
    );
  }
}

/// Preserves a block across selection groups, scoped to its owning renderer.
final class MarkdownBlockKey extends GlobalKey {
  const MarkdownBlockKey(this.owner, this.block) : super.constructor();

  final Object owner;
  final Object block;

  @override
  bool operator ==(Object other) =>
      other is MarkdownBlockKey &&
      identical(owner, other.owner) &&
      block == other.block;

  @override
  int get hashCode => Object.hash(owner, block);
}

class _BlockGroup extends StatefulWidget {
  const _BlockGroup({required this.children});

  final List<Widget> children;

  @override
  State<_BlockGroup> createState() => _BlockGroupState();
}

class _BlockGroupState extends State<_BlockGroup> {
  final _selection = StaticSelectionContainerDelegate();
  final _paragraphSelection = StaticSelectionContainerDelegate();

  @override
  void didUpdateWidget(covariant _BlockGroup oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.children.first.key != widget.children.first.key) {
      // An edit moved content between groups; its old selection is no longer valid.
      context
          .findAncestorStateOfType<SelectableRegionState>()
          ?.clearSelection();
    }
  }

  @override
  Widget build(BuildContext context) {
    final child = Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: widget.children,
    );
    final selectable = SelectionContainer.maybeOf(context) != null;
    return SelectionContainer(
      delegate: _selection,
      child: Builder(
        builder: (context) {
          // The outer container has now detached from any old SelectionArea.
          // Release handles while the inner container is still enabled.
          if (!selectable && _selection.value.hasSelection) {
            _selection.dispatchSelectionEvent(const ClearSelectionEvent());
          }
          return selectable
              ? SelectionContainer(delegate: _paragraphSelection, child: child)
              : SelectionContainer.disabled(child: child);
        },
      ),
    );
  }

  @override
  void dispose() {
    _paragraphSelection.dispose();
    _selection.dispose();
    super.dispose();
  }
}
