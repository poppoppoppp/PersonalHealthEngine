/// Visual QA for the redesigned history screen: data look-back + plain-language
/// change records. Golden captures require the same local fonts as widget_test.
library;

import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:phe_app/main.dart';
import 'package:phe_app/screens/history_screen.dart';
import 'package:phe_app/api_client.dart';

/// Only the surface the history screen touches; everything else is a no-op Fake.
class HistoryMockClient extends Fake implements L7Client {
  HistoryMockClient({required this.todayPayload});

  final Map<String, dynamic> todayPayload;

  @override
  Future<TodayPayload> getToday() async => TodayPayload(todayPayload);


  @override
  Future<Map<String, dynamic>> getEvidence() async => {
    'analysis_date': '2026-08-28',
    'metrics': [],
    'all_metrics': [
      {
        'key': 'sleep',
        'label': '睡眠',
        'value_display': '7 小时 18 分钟',
        'data_date': '2026-08-27',
        'freshness_status': 'RECENT',
        'freshness_label': '1 天前的数据',
        'used_in_judgment': true,
        'deviation_label': '低于个人近期基线',
        'baseline_median': 456.0,
        'baseline_value_display': '7 小时 36 分钟',
        'unit': 'seconds',
        'series': [
          for (var i = 0; i < 10; i++)
            {
              'local_date': '2026-08-${18 + i}'.toString(),
              'value_num': <double>[
                26200.0, 27100.0, 25400.0, 27800.0, 26300.0, 24800.0, 27400.0, 26100.0,
                26900.0, 26800.0,
              ][i],
            },
        ],
      },
      {
        'key': 'steps',
        'label': '步数',
        'value_display': '4,820 步',
        'data_date': '2026-08-28',
        'freshness_status': 'TODAY',
        'freshness_label': '今日数据',
        'used_in_judgment': true,
        'deviation_label': '低于个人近期基线',
        'baseline_median': 5900,
        'baseline_value_display': '5,900 步',
        'unit': 'steps',
        'series': [
          {'local_date': '2026-08-27', 'value_num': 6100},
          {'local_date': '2026-08-28', 'value_num': 4820},
        ],
      },
    ],
  };

  @override
  Future<Map<String, dynamic>> getSleepStructure({int days = 14}) async => {
    'nights': [
      {
        'local_date': '2026-08-28',
        'sleep_minutes': 473,
        'awake_minutes': 16,
        'total_minutes': 489,
        'awake_ratio': 0.032,
        'segment_count': 3,
      },
      {
        'local_date': '2026-08-27',
        'sleep_minutes': 344,
        'awake_minutes': 25,
        'total_minutes': 369,
        'awake_ratio': 0.068,
        'segment_count': 5,
      },
      {
        'local_date': '2026-08-26',
        'sleep_minutes': 431,
        'awake_minutes': 8,
        'total_minutes': 439,
        'awake_ratio': 0.018,
        'segment_count': 2,
      },
    ],
    'note': '两段结构',
  };

  @override
  Future<Map<String, dynamic>> getEpisodes({
    int? cursor,
    int limit = 30,
  }) async => {
    'episodes': [
      {
        'id': 2,
        'episode_key': 'UNKNOWN:2026-08-28',
        'start_date': '2026-08-28',
        'end_date': '2026-08-28',
        'phase': 'DEVELOPING',
        'summary': '身体出现一些变化，具体原因还不明确：从 8月28日 开始。',
      },
      {
        'id': 1,
        'episode_key': 'SLEEP_DEFICIT:2026-08-16',
        'start_date': '2026-08-16',
        'end_date': '2026-08-18',
        'phase': 'CLOSED',
        'summary': '睡眠不足：从 8月16日 开始，持续到 8月18日（共 3 天）。',
      },
    ],
    'next_cursor': null,
    'stable_days_hidden': 5,
    'note': null,
    'projection_version': 3,
  };
}

void main() {
  testWidgets('visual QA captures the redesigned history screen', (
    tester,
  ) async {
    final fontFile = File(r'C:\Windows\Fonts\simhei.ttf');
    final iconFontFile = File(
      r'D:\flutter\flutter\bin\cache\artifacts\material_fonts\materialicons-regular.otf',
    );
    if (!fontFile.existsSync() || !iconFontFile.existsSync()) return;
    await tester.runAsync(() async {
      final fontBytes = await fontFile.readAsBytes();
      final iconFontBytes = await iconFontFile.readAsBytes();
      final fontLoader = FontLoader('VisualChinese')
        ..addFont(Future.value(ByteData.view(fontBytes.buffer)));
      final iconFontLoader = FontLoader('MaterialIcons')
        ..addFont(Future.value(ByteData.view(iconFontBytes.buffer)));
      await Future.wait([fontLoader.load(), iconFontLoader.load()]);
    });
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    SharedPreferences.setMockInitialValues({});
    final prefs = await SharedPreferences.getInstance();
    final env = AppEnv(baseUrl: 'http://localhost:0', token: 't', preferences: prefs);
    env.client = HistoryMockClient(todayPayload: {
      'schema': 'l7.today/v1',
      'product_state': 'A',
      'headline': '今天整体稳定。',
    });

    await tester.pumpWidget(
      MaterialApp(
        theme: ThemeData(
          useMaterial3: true,
          colorSchemeSeed: const Color(0xFF33557A),
          scaffoldBackgroundColor: const Color(0xFFF7F8FA),
          fontFamily: 'VisualChinese',
          cardTheme: CardThemeData(
            color: Colors.white,
            elevation: 1,
            shadowColor: const Color(0x140F172A),
            surfaceTintColor: Colors.transparent,
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(16),
              side: const BorderSide(color: Color(0xFFE0E5EC)),
            ),
          ),
        ),
        home: HistoryScreen(env: env),
      ),
    );
    await tester.pumpAndSettle();
    await expectLater(
      find.byType(Scaffold),
      matchesGoldenFile('goldens/history_redesigned.png'),
    );
  });
}
