import 'package:conduit/shared/widgets/markdown/renderer/horizontal_overflow_fade.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';

Future<void> pumpOverflow(WidgetTester tester) async {
  await tester.pumpWidget(
    Directionality(
      textDirection: TextDirection.ltr,
      child: Center(
        child: SizedBox(
          width: 200,
          height: 80,
          child: HorizontalOverflowFade(
            scrollIndicatorColor: const Color(0xFF336699),
            builder: (context, controller) => SingleChildScrollView(
              controller: controller,
              scrollDirection: Axis.horizontal,
              child: const SizedBox(width: 600, height: 80),
            ),
          ),
        ),
      ),
    ),
  );
  await tester.pump();
  await tester.pump(HorizontalOverflowFade.scrollIndicatorFadeIn);
  // An animation completes on the first tick beyond its duration.
  await tester.pump(const Duration(milliseconds: 1));
}

double opacity(WidgetTester tester) {
  final dynamic state = tester.state(find.byType(HorizontalOverflowFade));
  return state.scrollIndicatorOpacity as double;
}

void main() {
  testWidgets('a visible indicator rests during its linger interval', (
    tester,
  ) async {
    await pumpOverflow(tester);
    expect(opacity(tester), 1);
    expect(tester.binding.hasScheduledFrame, isFalse);

    await tester.pump(const Duration(milliseconds: 798));
    expect(opacity(tester), 1);
    expect(tester.binding.hasScheduledFrame, isFalse);

    await tester.pump(const Duration(milliseconds: 1));
    await tester.pump(const Duration(milliseconds: 140));
    expect(opacity(tester), closeTo(1 - Curves.easeOut.transform(0.5), 0.001));
    await tester.pump(const Duration(milliseconds: 141));
    expect(opacity(tester), 0);
    expect(tester.binding.hasScheduledFrame, isFalse);
  });

  testWidgets('dragging interrupts dismissal and holds without frames', (
    tester,
  ) async {
    await pumpOverflow(tester);
    await tester.pump(HorizontalOverflowFade.scrollIndicatorLinger);
    await tester.pump(const Duration(milliseconds: 140));
    expect(opacity(tester), inExclusiveRange(0, 1));

    final gesture = await tester.startGesture(
      tester.getCenter(find.byType(HorizontalOverflowFade)),
    );
    await gesture.moveBy(const Offset(-60, 0));
    await tester.pump();
    expect(opacity(tester), 1);
    await tester.pump(const Duration(seconds: 2));
    expect(opacity(tester), 1);
    expect(tester.binding.hasScheduledFrame, isFalse);

    await gesture.up();
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 799));
    expect(opacity(tester), 1);
    await tester.pump(const Duration(milliseconds: 1));
    await tester.pumpAndSettle();
    expect(opacity(tester), 0);
  });

  testWidgets('disposing a lingering indicator cancels its dismissal', (
    tester,
  ) async {
    await pumpOverflow(tester);
    await tester.pumpWidget(const SizedBox());
    await tester.pump(const Duration(seconds: 2));
    expect(tester.takeException(), isNull);
    expect(tester.binding.hasScheduledFrame, isFalse);
  });
}
