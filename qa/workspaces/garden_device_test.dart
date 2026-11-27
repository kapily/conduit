import 'dart:convert';
import 'dart:io';

import 'package:conduit/main.dart' as conduit;
import 'package:conduit/features/deepseek_harness/deepseek_harness_page.dart';
import 'package:conduit/l10n/app_localizations.dart';
import 'package:conduit/l10n/conduit_localizations.dart';
import 'package:conduit_core/providers/storage_providers.dart';
import 'package:conduit_core/features/deepseek_harness/deepseek_harness.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:material_ui/material_ui.dart' show MaterialApp;
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
  for (var attempt = 1; ; attempt++) {
    final client = HttpClient()
      ..connectionTimeout = const Duration(seconds: 15);
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
      final response = await request.close().timeout(
        const Duration(minutes: 3),
      );
      if (response.statusCode != 200)
        throw StateError('Local garden controller rejected $path.');
      // Consume the body before destroying the short-lived test connection.
      return await utf8.decoder.bind(response).join();
    } on IOException {
      if (attempt == 3) rethrow;
    } finally {
      client.close(force: true);
    }
    await Future<void>.delayed(const Duration(milliseconds: 400));
  }
}

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();
  testWidgets('real iPhone creates and switches server workspaces', (
    tester,
  ) async {
    final input = tester.testTextInput;
    final installedInput = !input.isRegistered;
    if (installedInput) input.register();
    final testHandler = FlutterError.onError;
    final testErrorWidgetBuilder = ErrorWidget.builder;
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
      if (!ready()) {
        final flags = {
          'pair-rejected': 'Enter a fresh launch token',
          'pair-unavailable': 'connect to Harness. Check',
          'unauthenticated-empty-catalog': 'No conversations yet',
          'provider-error': 'Harness does not trust',
          'missing-session': 'Blue hour observing plan',
        };
        for (final entry in flags.entries) {
          if (find
              .textContaining(entry.value, findRichText: true)
              .evaluate()
              .isNotEmpty)
            await _control('mark-' + entry.key);
        }
        if (step == 'Connect' || step == 'authenticated sky sessions') {
          await _control('mark-wait-' + step.replaceAll(' ', '-'));
        }
        throw StateError('Garden check did not reach $step.');
      }
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
        await until(
          () => target.hitTestable().evaluate().isNotEmpty,
          'visible $label',
        );
        await tester.tap(target.hitTestable().first);
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
      final secret = find.byWidgetPredicate(
        (w) => w is EditableText && w.obscureText,
      );
      final container = ProviderScope.containerOf(
        tester.element(secret.first),
        listen: false,
      );
      await container.read(deepSeekHarnessConnectionProvider.future);
      final fixtureLaunch = Uri.parse(_origin)
          .replace(queryParameters: {'token': await _control('credential')});
      try {
        await container
            .read(deepSeekHarnessConnectionProvider.notifier)
            .connect(fixtureLaunch.toString());
      } on DeepSeekHarnessException catch (error) {
        await _control('mark-grant-' + error.reason.name);
        rethrow;
      }
      await _control('mark-grant-stored');
      await until(() => secret.evaluate().isEmpty, 'dismissed pairing form');
      await _control('mark-form-gone');
      final opener = find.byKey(const ValueKey('harness-workspaces-button'));
      final add = find.byKey(const ValueKey('harness-new-workspace'));
      Future<void> tapFinding(Finder finder, String name) async {
        await until(() => finder.evaluate().isNotEmpty, name);
        await tester.ensureVisible(finder.first);
        await until(
          () => finder.hitTestable().evaluate().isNotEmpty,
          'visible $name',
        );
        await tester.tap(finder.hitTestable().first);
        await tester.pump(const Duration(milliseconds: 500));
      }

      Future<void> drawer() => tapFinding(opener, 'open workspace drawer');
      Finder row(String value) => find.widgetWithText(AdaptiveListTile, value);
      await until(
        () =>
            seen('Blue hour observing plan') &&
            seen('Three crater sketches') &&
            secret.evaluate().isEmpty,
        'authenticated sky sessions',
      );
      await tester.pump(const Duration(milliseconds: 750));
      await _control('mark-catalog');
      await _control('paired');
      await drawer();
      await tapFinding(row('Lunar maps'), 'select existing lunar workspace');
      await until(
        () =>
            seen('Three crater sketches') && !seen('Blue hour observing plan'),
        'filtered lunar sessions',
      );
      await tester.pump(const Duration(milliseconds: 500));
      await _control('selected');
      await drawer();
      await until(
        () => add.evaluate().isNotEmpty && seen('Sky atlas'),
        'workspace sidebar',
      );
      await _control('folders');
      await tapFinding(add, 'new workspace');
      await press('New Folder');
      final folderName = find.byType(EditableText);
      await until(() => folderName.evaluate().isNotEmpty, 'new folder name');
      await tester.enterText(folderName.last, 'Meteor watch');
      await press('Create');
      await until(
        () => seen('No subfolders'),
        'newly created empty host directory',
      );
      await press('Use this folder');
      await until(
        () =>
            seen('Meteor watch') &&
            seen('No conversations yet') &&
            !seen('Three crater sketches'),
        'new selected workspace',
      );
      await press('New Chat');
      await until(
        () => composer.evaluate().isNotEmpty,
        'workspace chat composer',
      );
      final shell = tester
          .widgetList<AdaptiveRouteShell>(find.byType(AdaptiveRouteShell))
          .last;
      final back = shell.appBar?.leading;
      if (back is! ConduitAdaptiveAppBarIconButton ||
          back.semanticLabel != 'Chats' ||
          back.onPressed == null)
        throw StateError('Missing catalog navigation.');
      back.onPressed!();
      await until(
        () => opener.evaluate().isNotEmpty,
        'return to scoped catalog',
      );
      await drawer();
      await until(
        () =>
            add.evaluate().isNotEmpty &&
            seen('Sky atlas') &&
            seen('Lunar maps') &&
            seen('Meteor watch'),
        'three workspace options',
      );
      await tester.pump(const Duration(milliseconds: 500));
      await _control('verified');
      final backingStore = ProviderScope.containerOf(
        tester.element(add.first),
        listen: false,
      ).read(secureStorageProvider);
      final fresh = ProviderContainer(
        overrides: [secureStorageProvider.overrideWithValue(backingStore)],
      );
      try {
        await tester.pumpWidget(const SizedBox());
        await tester.pumpWidget(
          UncontrolledProviderScope(
            container: fresh,
            child: MaterialApp(
              localizationsDelegates: conduitLocalizationsDelegates,
              supportedLocales: AppLocalizations.supportedLocales,
              home: DeepSeekHarnessPage(
                onConnected: () async {},
                onDisconnected: () async {},
                onExit: () {},
                onOpenWorkspace: (_) async => false,
              ),
            ),
          ),
        );
        await until(
          () =>
              opener.evaluate().isNotEmpty &&
              seen('Meteor watch') &&
              !seen('No conversations yet') &&
              !seen('Blue hour observing plan') &&
              !seen('Three crater sketches'),
          'restored selected workspace and scoped catalog',
        );
        await _control('restored');
      } finally {
        await tester.pumpWidget(const SizedBox());
        fresh.dispose();
      }
    } finally {
      ErrorWidget.builder = testErrorWidgetBuilder;
      if (installedInput) input.unregister();
      FlutterError.onError = testHandler;
      dispatcher.onError = platformHandler;
    }
  }, timeout: const Timeout(Duration(minutes: 7)));
}
