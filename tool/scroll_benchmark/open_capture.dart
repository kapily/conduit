import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:vm_service/vm_service_io.dart';

Future<void> main(List<String> args) async {
  final root = args[0];
  final label = args[1];
  final tap =
      jsonDecode(File('$root/selection-tap.json').readAsStringSync()) as List;
  final service = await vmServiceConnectUri(
    File('$root/vm-host-uri').readAsStringSync(),
  );
  final frames = <Map<String, dynamic>>[];
  final subscription = service.onExtensionEvent.listen((event) {
    if (event.extensionKind == 'Flutter.Frame') {
      frames.add(Map<String, dynamic>.from(event.extensionData!.data));
    }
  });
  await service.streamListen('Extension');
  final profile = args.contains('--cpu');
  final isolate = (await service.getVM()).isolates!.firstWhere(
    (i) => i.name == 'main',
  );
  await service.setFlag('profiler', profile ? 'true' : 'false');
  if (profile) await service.clearCpuSamples(isolate.id!);
  await service.setVMTimelineFlags(profile ? ['Dart'] : []);
  await service.clearVMTimeline();
  final adb = '${Platform.environment['ANDROID_HOME']}/platform-tools/adb';
  final pid = (await Process.run(adb, [
    '-s',
    'emulator-5554',
    'shell',
    'pidof',
    'app.cogwheel.conduit',
  ])).stdout.toString().trim();
  Future<Map<String, Object?>> memory() async => {
    'vmMicros': (await service.getVMTimelineMicros()).timestamp,
    'hostProcStat': File(
      '/proc/${Platform.environment['BENCH_EMULATOR_PID']}/stat',
    ).readAsStringSync(),
    'hostProcStatus': File(
      '/proc/${Platform.environment['BENCH_EMULATOR_PID']}/status',
    ).readAsLinesSync().where((s) => s.startsWith('Vm')).toList(),
    'dart': (await service.getMemoryUsage(isolate.id!)).toJson(),
    'procStat': (await Process.run(adb, [
      '-s',
      'emulator-5554',
      'shell',
      'cat',
      '/proc/$pid/stat',
    ])).stdout,
    'procStatus':
        (await Process.run(adb, [
              '-s',
              'emulator-5554',
              'shell',
              'cat',
              '/proc/$pid/status',
            ])).stdout
            .toString()
            .split('\n')
            .where((s) => s.startsWith('Vm') || s.startsWith('Threads:'))
            .toList(),
  };
  final before = await memory();
  final start = (await service.getVMTimelineMicros()).timestamp!;
  try {
    final result = await Process.run(adb, [
      '-s',
      'emulator-5554',
      'shell',
      'input',
      'tap',
      '${tap[0]}',
      '${tap[1]}',
    ]);
    if (result.exitCode != 0) throw StateError('Selection failed');
    await Future<void>.delayed(const Duration(seconds: 8));
    final end = (await service.getVMTimelineMicros()).timestamp!;
    final after = await memory();
    await service.getAllocationProfile(isolate.id!, gc: true);
    await service.getAllocationProfile(isolate.id!, gc: true);
    final retained = await memory();
    File('$root/$label-memory.json')
        .writeAsStringSync(jsonEncode([before, after, retained]));
    final timeline = args.contains('--system-trace')
        ? {'recorder': 'Systrace', 'eventsStoredExternally': true}
        : (await service.getVMTimeline()).toJson();
    if (profile) {
      File('$root/$label-cpu.json').writeAsStringSync(
        jsonEncode(
          (await service.getCpuSamples(
            isolate.id!,
            start,
            end - start,
          )).toJson(),
        ),
      );
    }
    File('$root/$label-timeline.json').writeAsStringSync(
      jsonEncode({
        'chunks': [timeline],
      }),
    );
    File('$root/$label-frame-timings.json').writeAsStringSync(
      jsonEncode({
        'frames': frames
            .where((f) => f['startTime'] >= start && f['startTime'] <= end)
            .toList(),
        'gestures': [
          {'start': start, 'end': end, 'presentations': <int>[]},
        ],
      }),
    );
  } finally {
    await service.setFlag('profiler', 'false');
    await service.setVMTimelineFlags([]);
    await subscription.cancel();
    await service.dispose();
  }
  print('Captured ${frames.length} opening frames locally');
}
