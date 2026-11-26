import 'dart:convert';
import 'dart:io';

import 'package:conduit/main.dart' as conduit;
import 'package:conduit/shared/widgets/adaptive_route_shell.dart';
import 'package:conduit/shared/widgets/adaptive_toolbar_components.dart';
import 'package:conduit/shared/widgets/platform_ui/platform_ui.dart';
import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

const _origin = 'http://127.0.0.1:43671/';
const _controlPort = 43673;
const _question = 'Give me a checklist for a tray of basil seedlings.';
const _answer = [
  'Label each pot.',
  'Water the seedlings.',
  'Check the soil again tomorrow.',
];

Future<String> _control(String path) async {
  final client = HttpClient()..connectionTimeout = const Duration(seconds: 5);
  client.findProxy = (_) => 'DIRECT';
  try {
    final uri = Uri(
      scheme: 'http',
      host: '127.0.0.1',
      port: _controlPort,
      path: '/$path',
    );
    final request = await client.postUrl(uri);
    request.followRedirects = false;
    request.headers.set(
      'X-Garden-Run',
      const String.fromEnvironment('GARDEN_RUN'),
    );
    request.contentLength = 0;
    final response = await request.close().timeout(const Duration(minutes: 3));
    if (response.statusCode != 200)
      throw StateError('Local garden controller rejected $path.');
    return utf8.decoder.bind(response).join();
  } finally {
    client.close(force: true);
  }
}

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();
  testWidgets('real device pairs and reopens a garden checklist', (
    tester,
  ) async {
    final input = tester.testTextInput;
    final installedInput = !input.isRegistered;
    if (installedInput) input.register();
    final testHandler = FlutterError.onError;
    final dispatcher = WidgetsBinding.instance.platformDispatcher;
    final platformHandler = dispatcher.onError;
    final chooser = find.byKey(const Key('backend-chooser-deepseek-harness'));
    final fields = find.byType(EditableText);
    final composer = find.descendant(
      of: find.byKey(const ValueKey('harness-message-field')),
      matching: fields,
    );
    Future<void> until(bool Function() ready, String step) async {
      final watch = Stopwatch()..start();
      while (!ready() && watch.elapsed < const Duration(seconds: 60)) {
        await tester.pump(const Duration(milliseconds: 150));
      }
      if (!ready()) throw StateError('Garden check did not reach $step.');
    }

    bool seen(String s) =>
        find.textContaining(s, findRichText: true).evaluate().isNotEmpty;
    Finder button(String label) => find.byWidgetPredicate(
      (widget) =>
          widget is AdaptiveButton &&
          (widget.semanticLabel == label || widget.label == label) &&
          widget.onPressed != null,
    );
    Future<void> press(String label) async {
      final target = button(label);
      await until(() => target.evaluate().isNotEmpty, label);
      await tester.ensureVisible(target.first);
      // The iOS native buttons may own their own PlatformView gesture surface.
      // Dispatch its bound production action there, and tap real Flutter buttons elsewhere.
      final native = find.descendant(
        of: target.first,
        matching: find.byType(UiKitView),
      );
      if (Platform.isIOS && native.evaluate().isNotEmpty) {
        tester.widget<AdaptiveButton>(target.first).onPressed!();
      } else {
        await tester.tap(target.first);
      }
    }

    try {
      conduit.main();
      await until(
        () => chooser.evaluate().isNotEmpty,
        'production backend chooser',
      );
      FlutterError.onError = testHandler;
      dispatcher.onError = platformHandler;
      await tester.ensureVisible(chooser);
      await tester.tap(chooser);
      await until(() => fields.evaluate().length == 2, 'pairing form');
      final address = find.byWidgetPredicate(
        (w) => w is EditableText && !w.obscureText,
      );
      final secret = find.byWidgetPredicate(
        (w) => w is EditableText && w.obscureText,
      );
      if (secret.evaluate().length != 1 || address.evaluate().length != 1) {
        throw StateError('The concealed token field was unavailable.');
      }
      await tester.enterText(address, _origin);
      await tester.enterText(secret, await _control('credential'));
      await press('Connect');
      await until(
        () =>
            seen('No conversations yet') &&
            button('New Chat').evaluate().isNotEmpty &&
            secret.evaluate().isEmpty &&
            !seen('Launch token'),
        'authenticated empty native catalog',
      );
      await tester.pump(const Duration(milliseconds: 750));
      await _control('paired');
      await press('New Chat');
      await until(() => composer.evaluate().isNotEmpty, 'native composer');
      await tester.enterText(composer, _question);
      await press('Send');
      await until(
        () => seen(_question) && _answer.every(seen),
        'complete server reply',
      );
      await tester.pump(const Duration(milliseconds: 900));
      await _control('reply');
      final shell = tester
          .widgetList<AdaptiveRouteShell>(find.byType(AdaptiveRouteShell))
          .last;
      final back = shell.appBar?.leading;
      if (back is! ConduitAdaptiveAppBarIconButton ||
          back.semanticLabel != 'Chats' ||
          back.onPressed == null) {
        throw StateError('Production chat navigation was unavailable.');
      }
      final backWidget = find.byWidget(back);
      if (Platform.isIOS || backWidget.evaluate().isEmpty) {
        back.onPressed!();
      } else {
        await tester.tap(backWidget);
      }
      // The private server has exactly one chat, created by our preceding request.
      final row = find.byWidgetPredicate(
        (w) => w is AdaptiveListTile && w.key is ValueKey<String>,
      );
      await until(
        () => row.evaluate().length == 1 && composer.evaluate().isEmpty,
        'server session in native history',
      );
      await tester.ensureVisible(row);
      await tester.tap(row);
      await until(
        () =>
            composer.evaluate().isNotEmpty &&
            seen(_question) &&
            _answer.every(seen),
        'reopened server history',
      );
      await tester.pump(const Duration(milliseconds: 600));
      await _control('history');
    } finally {
      if (installedInput) input.unregister();
      FlutterError.onError = testHandler;
      dispatcher.onError = platformHandler;
    }
  }, timeout: const Timeout(Duration(minutes: 7)));
}
