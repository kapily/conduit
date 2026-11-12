import 'package:conduit/shared/widgets/markdown/renderer/horizontal_overflow_fade.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('expired indicator does not rebuild content when tabs switch', (
    tester,
  ) async {
    final active = ValueNotifier(true);
    addTearDown(active.dispose);
    var builds = 0;
    await tester.pumpWidget(
      ValueListenableBuilder<bool>(
        valueListenable: active,
        builder: (context, enabled, child) =>
            TickerMode(enabled: enabled, child: child!),
        child: Directionality(
          textDirection: TextDirection.ltr,
          child: Center(
            child: SizedBox(
              width: 200,
              height: 80,
              child: HorizontalOverflowFade(
                scrollIndicatorColor: const Color(0xFF336699),
                builder: (context, controller) {
                  builds++;
                  return SingleChildScrollView(
                    controller: controller,
                    scrollDirection: Axis.horizontal,
                    child: const SizedBox(width: 600, height: 80),
                  );
                },
              ),
            ),
          ),
        ),
      ),
    );
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 1201));
    await tester.pumpAndSettle();
    builds = 0;
    active.value = false;
    await tester.pump();
    expect(builds, 0);
    active.value = true;
    await tester.pump();
    expect(builds, 0);
    await tester.pumpWidget(const SizedBox());
  });
}
