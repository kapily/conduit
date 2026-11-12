import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:vm_service/vm_service_io.dart';

Future<void> main(List<String> args) async {
  final root = Directory(args[0]);
  final label = args[1];
  final count = int.parse(args[2]);
  final profile = args.contains('--cpu');
  final adbPath = '${Platform.environment['ANDROID_HOME']}/platform-tools/adb';
  Future<String> adb(List<String> command) async {
    final result = await Process.run(adbPath, [
      '-s',
      'emulator-5554',
      ...command,
    ]);
    if (result.exitCode != 0) throw StateError('ADB failed: ${result.stderr}');
    return result.stdout as String;
  }

  void save(String suffix, Object? data) =>
      File('${root.path}/$label-$suffix.json')
          .writeAsStringSync(jsonEncode(data));
  final service = await vmServiceConnectUri(
    File('${root.path}/vm-host-uri').readAsStringSync().trim(),
  );
  final vm = await service.getVM();
  final main = vm.isolates!.firstWhere((i) => i.name == 'main').id!;
  final pid = (await adb(['shell', 'pidof', 'app.cogwheel.conduit'])).trim();
  final layers = await adb(['shell', 'dumpsys', 'SurfaceFlinger', '--list']);
  final layer = RegExp(
    r'SurfaceView\[app\.cogwheel\.conduit/[^\]]+\]\(BLAST\)#[0-9]+',
  ).firstMatch(layers)!.group(0)!;
  Future<List<int>> presentations() async {
    final text = await adb([
      'shell',
      "dumpsys SurfaceFlinger --latency '$layer'",
    ]);
    final stamps = <int>{};
    for (final line in text.split('\n').skip(1)) {
      final fields = line.trim().split(RegExp(r'\s+'));
      if (fields.length != 3) continue;
      final value = int.tryParse(fields[1]);
      if (value != null && value > 0 && value < 4611686018427387904)
        stamps.add(value);
    }
    return stamps.toList()..sort();
  }

  Future<Map<String, Object?>> memory() async => {
    'vmMicros': (await service.getVMTimelineMicros()).timestamp,
    'hostProcStat': File(
      '/proc/${Platform.environment['BENCH_EMULATOR_PID']}/stat',
    ).readAsStringSync(),
    'hostProcStatus': File(
      '/proc/${Platform.environment['BENCH_EMULATOR_PID']}/status',
    ).readAsLinesSync().where((s) => s.startsWith('Vm')).toList(),
    'dart': (await service.getMemoryUsage(main)).toJson(),
    'procStat': await adb(['shell', 'cat', '/proc/$pid/stat']),
    'procStatus': (await adb(['shell', 'cat', '/proc/$pid/status']))
        .split('\n')
        .where((s) => s.startsWith('Vm') || s.startsWith('Threads:'))
        .toList(),
  };
  final frames = <Map<String, dynamic>>[];
  final gestures = <Map<String, Object?>>[];
  final memories = <Map<String, Object?>>[];
  final subscription = service.onExtensionEvent.listen((event) {
    if (event.extensionKind == 'Flutter.Frame') {
      frames.add(Map<String, dynamic>.from(event.extensionData!.data));
    }
  });
  await service.streamListen('Extension');
  await service.setFlag('profiler', profile.toString());
  await service.setVMTimelineFlags(profile ? ['Dart', 'GC'] : []);
  await service.callServiceExtension(
    'ext.flutter.profileWidgetBuilds',
    isolateId: main,
    args: {'enabled': 'false'},
  );
  if (profile) {
    await service.clearCpuSamples(main);
    await service.clearVMTimeline();
  }
  final start = (await service.getVMTimelineMicros()).timestamp!;
  try {
    memories.add(await memory());
    for (var n = 0; n < count; n++) {
      final older = (n ~/ 6).isEven;
      final before = await presentations();
      final begin = (await service.getVMTimelineMicros()).timestamp!;
      final input = await adb([
        'shell',
        'CLASSPATH=/data/local/tmp/conduit-scroll-input.dex',
        'app_process',
        '/system/bin',
        'ScrollInput',
        '520',
        older ? '650' : '1800',
        '520',
        older ? '1800' : '650',
        '1000',
        '120',
      ]);
      final end = (await service.getVMTimelineMicros()).timestamp!;
      gestures.add({
        'index': n,
        'direction': older ? 'older' : 'newer',
        'start': begin,
        'end': end,
        'injection': jsonDecode(input.trim()),
        'previousPresentation': before.isEmpty ? null : before.last,
        'presentations': (await presentations())
            .where((t) => before.isEmpty || t > before.last)
            .toList(),
      });
      memories.add(await memory());
      if (n % 6 == 5) print('Captured ${n + 1} gestures');
    }
    final end = (await service.getVMTimelineMicros()).timestamp!;
    await Future<void>.delayed(const Duration(seconds: 1));
    save('frames', {
      'profiled': profile,
      'frames': frames,
      'gestures': gestures,
    });
    save('memory', memories);
    await service.getAllocationProfile(main, gc: true);
    await service.getAllocationProfile(main, gc: true);
    save('retained-memory', await memory());
    if (profile) {
      save(
        'cpu',
        (await service.getCpuSamples(main, start, end - start)).toJson(),
      );
      if (!args.contains('--system-trace')) {
        save(
          'timeline',
          (await service.getVMTimeline(
            timeOriginMicros: start,
            timeExtentMicros: end - start,
          )).toJson(),
        );
      }
    }
  } finally {
    await service.setFlag('profiler', 'false');
    await service.setVMTimelineFlags([]);
    await subscription.cancel();
    await service.dispose();
  }
  print('Captured ${frames.length} frames');
}
