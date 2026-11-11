import 'package:checks/checks.dart';
import 'package:conduit/l10n/app_localizations.dart';
import 'package:conduit/l10n/conduit_localizations.dart';
import 'package:conduit/shared/theme/app_theme.dart';
import 'package:conduit/shared/theme/tweakcn_themes.dart';
import 'package:conduit/shared/widgets/markdown/markdown_compile_service.dart';
import 'package:conduit/shared/widgets/markdown/renderer/chunked_block_column.dart';
import 'package:conduit/shared/widgets/markdown/renderer/conduit_markdown_widget.dart';
import 'package:conduit/shared/widgets/markdown/streaming_markdown_widget.dart';
import 'package:conduit/shared/widgets/platform_ui/platform_ui.dart';
import 'package:flutter/rendering.dart';
import 'package:material_ui/material_ui.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_test/flutter_test.dart';

/// Past the block threshold and, with ChunkedBlockColumn's default chunk
/// sizes (48 first, then 96 per frame), still two reveal frames; larger
/// documents only add layout time.
const int _largeParagraphCount = markdownChunkedPartInflationThreshold + 32;
const String _lastParagraph = 'Paragraph ${_largeParagraphCount - 1}';

String _manyParagraphs(int paragraphCount) {
  final buffer = StringBuffer();
  for (var index = 0; index < paragraphCount; index += 1) {
    buffer.writeln('Paragraph $index with some **bold** and `code` text.');
    buffer.writeln();
  }
  return buffer.toString();
}

Widget _harness(
  Widget child, {
  double width = 400,
  TargetPlatform platform = TargetPlatform.android,
}) {
  return ProviderScope(
    child: MaterialApp(
      theme: AppTheme.light(TweakcnThemes.t3Chat).copyWith(platform: platform),
      localizationsDelegates: conduitLocalizationsDelegates,
      supportedLocales: AppLocalizations.supportedLocales,
      home: Scaffold(
        body: SingleChildScrollView(
          child: SizedBox(width: width, child: child),
        ),
      ),
    ),
  );
}

void main() {
  for (final (platform, width) in [
    (TargetPlatform.android, 360.0),
    (TargetPlatform.android, 1024.0),
    (TargetPlatform.iOS, 390.0),
    (TargetPlatform.iOS, 1024.0),
  ]) {
    testWidgets(
      'keeps revealed paragraph layout on $platform at width $width',
      (tester) async {
        const paragraphCount = 1024;
        PlatformUiCapabilities.debugPlatformOverride = platform;
        addTearDown(PlatformUiCapabilities.resetDebugOverrides);
        tester.view.devicePixelRatio = 1;
        tester.view.physicalSize = Size(width, 900);
        addTearDown(tester.view.resetDevicePixelRatio);
        addTearDown(tester.view.resetPhysicalSize);
        await tester.pumpWidget(
          _harness(
            StreamingMarkdownWidget(
              content: _manyParagraphs(paragraphCount),
              isStreaming: false,
            ),
            width: width,
            platform: platform,
          ),
        );
        final firstParagraph = find.byWidgetPredicate(
          (widget) =>
              widget is RichText &&
              widget.text.toPlainText().startsWith('Paragraph 0 '),
        );
        final paragraph = tester.renderObject<RenderParagraph>(firstParagraph);
        // Nested iOS selection containers register in successive frames.
        for (var frame = 0; frame < 3; frame++) {
          await tester.pump();
          expect(
            tester.renderObject<RenderParagraph>(firstParagraph),
            same(paragraph),
          );
        }
        tester
            .state<SelectableRegionState>(find.byType(SelectableRegion))
            .selectAll(SelectionChangedCause.keyboard);
        final selection = tester.widget<SelectionContainer>(
          find.byType(SelectionContainer).first,
        );
        expect(
          selection.delegate!.getSelectedContent()?.plainText,
          contains('Paragraph 0 '),
        );
        for (var frame = 0; frame < 12; frame++) {
          await tester.pump(const Duration(milliseconds: 16));
          expect(
            tester.renderObject<RenderParagraph>(firstParagraph),
            same(paragraph),
          );
          expect(tester.takeException(), isNull);
        }
        expect(
          find.textContaining(
            'Paragraph ${paragraphCount - 1}',
            findRichText: true,
          ),
          findsWidgets,
        );
        expect(find.byType(SelectionArea), findsOneWidget);
        expect(
          selection.delegate!.getSelectedContent()?.plainText,
          contains('Paragraph 0 '),
        );
        tester
            .state<SelectableRegionState>(find.byType(SelectableRegion))
            .selectAll(SelectionChangedCause.keyboard);
        final copied = selection.delegate!.getSelectedContent()!.plainText;
        expect(
          RegExp(r'Paragraph (\d+) ')
              .allMatches(copied)
              .map((match) => int.parse(match.group(1)!)),
          List.generate(paragraphCount, (index) => index),
        );
        for (final streaming in [true, false]) {
          await tester.pumpWidget(
            _harness(
              StreamingMarkdownWidget(
                content: _manyParagraphs(paragraphCount),
                isStreaming: streaming,
              ),
              width: width,
              platform: platform,
            ),
          );
          await tester.pumpAndSettle();
          expect(tester.takeException(), isNull);
        }
      },
    );
  }

  testWidgets('retains state when selection and reveal batch size change', (
    tester,
  ) async {
    final contentKey = GlobalKey();
    final markerKey = GlobalKey();
    final children = <Widget>[
      for (var index = 0; index < 160; index++) Text('Block $index'),
      StatefulBuilder(
        key: markerKey,
        builder: (context, setState) => const Text('Later block'),
      ),
    ];
    State? markerState;
    for (final selectable in [false, true, false, true]) {
      final body = KeyedSubtree(
        key: contentKey,
        child: ChunkedBlockColumn(
          chunkSize: selectable ? 64 : 96,
          children: children,
        ),
      );
      await tester.pumpWidget(
        _harness(selectable ? SelectionArea(child: body) : body),
      );
      await tester.pumpAndSettle();
      expect(tester.takeException(), isNull);
      markerState ??= markerKey.currentState!;
      expect(markerKey.currentState, same(markerState));
    }
  });

  for (final wrapped in [false, true]) {
    testWidgets(
      'retains block state across insertions (identity wrapper: $wrapped)',
      (tester) async {
        const markerKey = ValueKey('retained-block');
        final marker = StatefulBuilder(
          key: markerKey,
          builder: (context, setState) => const Text('Later block'),
        );
        final children = <Widget>[
          for (var index = 0; index < 127; index++)
            Text('Block $index', key: ValueKey(index)),
          wrapped
              ? KeyedSubtree(key: const ValueKey('identity'), child: marker)
              : marker,
          for (var index = 128; index < 160; index++)
            Text('Block $index', key: ValueKey(index)),
        ];
        await tester.pumpWidget(
          _harness(
            SelectionArea(child: ChunkedBlockColumn(children: children)),
          ),
        );
        await tester.pumpAndSettle();
        final markerState = tester.state(find.byKey(markerKey));
        tester
            .state<SelectableRegionState>(find.byType(SelectableRegion))
            .selectAll(SelectionChangedCause.keyboard);
        for (final updated in [
          [const Text('Inserted block', key: ValueKey('new')), ...children],
          children,
        ]) {
          await tester.pumpWidget(
            _harness(
              SelectionArea(child: ChunkedBlockColumn(children: updated)),
            ),
          );
          await tester.pumpAndSettle();
          expect(tester.takeException(), isNull);
          expect(tester.state(find.byKey(markerKey)), same(markerState));
        }
      },
    );
  }

  testWidgets('large settled documents inflate blocks across frames', (
    tester,
  ) async {
    final prepared = prepareMarkdownContent(
      _manyParagraphs(_largeParagraphCount),
      streaming: false,
    );
    final document = compilePreparedMarkdownSync(prepared);
    check(
      document.blocks.length,
    ).isGreaterThan(markdownChunkedPartInflationThreshold);

    await tester.pumpWidget(
      _harness(ConduitMarkdownWidget(compiledDocument: document)),
    );

    final chunkedFinder = find.byType(ChunkedBlockColumn);
    check(tester.any(chunkedFinder)).isTrue();

    // First frame reveals only the initial chunk.
    final initiallyVisible = find.textContaining(
      'Paragraph 0 ',
      findRichText: true,
    );
    check(tester.any(initiallyVisible)).isTrue();
    check(
      tester.any(find.textContaining(_lastParagraph, findRichText: true)),
    ).isFalse();

    // Subsequent frames stream in the rest; settle bounds the reveal loop.
    await tester.pumpAndSettle();
    check(
      tester.any(find.textContaining(_lastParagraph, findRichText: true)),
    ).isTrue();
  });

  testWidgets('small documents keep the single-frame column', (tester) async {
    final prepared = prepareMarkdownContent(
      _manyParagraphs(10),
      streaming: false,
    );
    final document = compilePreparedMarkdownSync(prepared);

    await tester.pumpWidget(
      _harness(ConduitMarkdownWidget(compiledDocument: document)),
    );

    check(tester.any(find.byType(ChunkedBlockColumn))).isFalse();
    check(
      tester.any(find.textContaining('Paragraph 9 ', findRichText: true)),
    ).isTrue();
  });

  testWidgets(
    'settled chat bodies chunk the display-part column',
    (tester) async {
      // The chat path splits a document into ONE display part per root block,
      // so per-part documents never cross ConduitMarkdownWidget's own block
      // threshold — the parts column itself must chunk (the 5.4s freeze).
      await tester.pumpWidget(
        _harness(
          StreamingMarkdownWidget(
            content: _manyParagraphs(_largeParagraphCount),
            isStreaming: false,
          ),
        ),
      );

      check(tester.any(find.byType(ChunkedBlockColumn))).isTrue();
      check(
        tester.any(find.textContaining('Paragraph 0 ', findRichText: true)),
      ).isTrue();
      check(
        tester.any(find.textContaining(_lastParagraph, findRichText: true)),
      ).isFalse();

      await tester.pumpAndSettle();
      check(
        tester.any(find.textContaining(_lastParagraph, findRichText: true)),
      ).isTrue();
    },
  );
}
