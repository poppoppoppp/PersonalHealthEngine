/// L7 app tests: payload parsing + Today-first presentation rules.
/// Contract assertions: five states, no scores, ≤3 actions, stable day = 0 actions,
/// medical-safety order = conclusion → action → cause.
library;

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:phe_app/api_client.dart';
import 'package:phe_app/main.dart';
import 'package:phe_app/read_cache.dart';
import 'package:phe_app/screens/context_screen.dart';
import 'package:phe_app/screens/evidence_screen.dart';
import 'package:phe_app/screens/history_screen.dart';
import 'package:phe_app/screens/me_screen.dart';
import 'package:phe_app/screens/notifications_screen.dart';
import 'package:phe_app/screens/patterns_screen.dart';
import 'package:phe_app/screens/qa_screen.dart';
import 'package:phe_app/screens/today_screen.dart';

class FakeClient implements L7Client {
  final Map<String, dynamic> today;
  FakeClient(this.today);

  @override
  Future<TodayPayload> getToday() async => TodayPayload(today);

  @override
  Future<Map<String, dynamic>> refreshToday() async => {'today': today};

  @override
  Future<Map<String, dynamic>> getTodayVersions() async => {'versions': []};

  @override
  Future<Map<String, dynamic>> getEvidence() async => {'metrics': []};

  @override
  Future<Map<String, dynamic>> getPatterns() async => {
    'patterns': [],
    'observing_count': 0,
  };

  @override
  Future<Map<String, dynamic>> getSettings() async => {
    'settings': {'notification_mode': 'SMART'},
  };

  @override
  Future<Map<String, dynamic>> putSettings(values) async => {
    'settings': values,
  };

  @override
  Future<Map<String, dynamic>> getUsage() async => {'eval_runs': 0};

  @override
  Future<Map<String, dynamic>> health() async => {'status': 'ok'};

  @override
  Future<Map<String, dynamic>> qaOpenConversation() async => {
    'conversation_id': 1,
  };

  @override
  Future<Map<String, dynamic>> qaConversation(
    int id, {
    int? cursor,
    int limit = 30,
  }) async => {'conversation_id': id, 'turns': []};

  @override
  Future<Map<String, dynamic>> qaAsk(
    String question, {
    int? conversationId,
  }) async => {
    'conversation_id': conversationId ?? 1,
    'direct_answer': '基于你的证据包的回答',
    'actions': const <String>[],
    'scope': 'HEALTH',
    'medical_review_state': 'BYPASSED',
    'evidence_ref': {'grounded': true},
  };

  @override
  Future<Map<String, dynamic>> listContext({
    int? cursor,
    int limit = 30,
  }) async => {'contexts': [], 'next_cursor': null};

  @override
  Future<Map<String, dynamic>> addContext(
    String text, {
    String? idempotencyKey,
  }) async => {'accepted': true, 'job_id': 1, 'status': 'PENDING'};

  @override
  Future<Map<String, dynamic>> correctContext(
    int id,
    String text, {
    String? idempotencyKey,
  }) async => {'accepted': true, 'job_id': 1, 'status': 'PENDING'};

  @override
  Future<Map<String, dynamic>> deleteContext(
    int id, {
    String? idempotencyKey,
  }) async => {'accepted': true, 'job_id': 1, 'status': 'PENDING'};

  @override
  Future<Map<String, dynamic>> submitFeedback(
    String verdict, {
    String? text,
    String? idempotencyKey,
  }) async => {'accepted': true, 'job_id': 1, 'status': 'PENDING'};

  @override
  Future<Map<String, dynamic>> getJobStatus(int id) async => {
    'id': id,
    'status': 'SUCCEEDED',
    'result_version': 1,
  };

  @override
  Future<Map<String, dynamic>> getEpisodes({
    int? cursor,
    int limit = 30,
  }) async => {'episodes': [], 'stable_days_hidden': 0, 'next_cursor': null};

  @override
  Future<Map<String, dynamic>> getEpisode(
    int id, {
    int? cursor,
    int limit = 30,
  }) async => {'episode': {}, 'timeline': [], 'next_cursor': null};

  @override
  Future<Map<String, dynamic>> searchHistory(String q) async => {'results': []};

  @override
  Future<Map<String, dynamic>> getNotifications() async => {
    'notifications': [],
  };

  @override
  Future<Map<String, dynamic>> getNotificationDecisions() async => {
    'decisions': [],
  };
}

class FailingClient extends FakeClient {
  FailingClient(super.today);

  Never _failure() =>
      throw const ApiException(ApiErrorKind.authentication, '认证失败，请更新访问令牌');

  @override
  Future<TodayPayload> getToday() async => _failure();

  @override
  Future<Map<String, dynamic>> refreshToday() async => _failure();

  @override
  Future<Map<String, dynamic>> getEvidence() async => _failure();

  @override
  Future<Map<String, dynamic>> getEpisodes({
    int? cursor,
    int limit = 30,
  }) async => _failure();

  @override
  Future<Map<String, dynamic>> getPatterns() async => _failure();

  @override
  Future<Map<String, dynamic>> getSettings() async => _failure();

  @override
  Future<Map<String, dynamic>> listContext({
    int? cursor,
    int limit = 30,
  }) async => _failure();

  @override
  Future<Map<String, dynamic>> getNotifications() async => _failure();

  @override
  Future<Map<String, dynamic>> qaAsk(
    String question, {
    int? conversationId,
  }) async => _failure();
}

class PendingQnAClient extends FakeClient {
  final qaCompleter = Completer<Map<String, dynamic>>();

  PendingQnAClient(super.today);

  @override
  Future<Map<String, dynamic>> qaAsk(String question, {int? conversationId}) =>
      qaCompleter.future;
}

class ProductConformanceClient extends FakeClient {
  ProductConformanceClient(super.today);

  @override
  Future<Map<String, dynamic>> getTodayVersions() async => {
    'versions': [
      {
        'id': 2,
        'analysis_date': '2026-08-24',
        'product_state': 'E',
        'product_state_label': '健康安全关注',
        'created_at_utc': '2026-08-24T02:00:00+00:00',
        'trigger': 'app_open',
        'trigger_label': '打开应用',
        'judgment_updated': 0,
      },
    ],
  };

  @override
  Future<Map<String, dynamic>> getEvidence() async => {
    'analysis_date': '2026-08-24',
    'provenance_note': '精确证据链',
    'metrics': [
      {
        'feature_label': 'REM 睡眠占比',
        'metric_label': 'REM 睡眠占比',
        'deviation_class': 'BELOW_TYPICAL_RANGE',
        'deviation_label': '低于个人近期基线',
        'baseline_maturity': 'PROVISIONAL',
        'baseline_maturity_label': '个人基线初步可用',
        'evidence_status': 'PROVISIONAL',
        'evidence_status_label': '初步证据',
        'current_value_display': '12.0%',
        'baseline_value_display': '20.0%',
        'feature_date': '2026-08-22',
        'freshness_label': '2 天前的数据',
        'series': const <dynamic>[],
        'baselines': const <dynamic>[],
        'deviations': const <dynamic>[],
      },
    ],
  };

  @override
  Future<Map<String, dynamic>> listContext({
    int? cursor,
    int limit = 30,
  }) async => {
    'contexts': [
      {
        'id': 1,
        'context_type': 'HIGH_INTENSITY_TRAINING',
        'context_type_label': '高强度训练',
        'body_part': 'LEG',
        'body_part_label': '腿部',
        'context_date': '2026-08-24',
        'raw_text': '昨天练腿很累',
      },
    ],
  };

  @override
  Future<Map<String, dynamic>> getEpisodes({
    int? cursor,
    int limit = 30,
  }) async => {
    'episodes': [
      {
        'id': 1,
        'start_date': '2026-08-24',
        'end_date': '2026-08-24',
        'phase': 'DEVELOPING',
        'summary': '恢复压力：2026-08-24 起。',
      },
    ],
    'stable_days_hidden': 0,
    'next_cursor': null,
  };

  @override
  Future<Map<String, dynamic>> getEpisode(
    int id, {
    int? cursor,
    int limit = 30,
  }) async => {
    'episode': {'summary': '恢复压力：2026-08-24 起。'},
    'timeline': [
      {
        'event_date': '2026-08-24',
        'kind': 'JUDGMENT',
        'kind_label': '健康判断',
        'detail': {
          'overall_state': 'NOTABLE_CHANGE',
          'overall_state_label': '变化较明显',
          'primary': 'RECOVERY_STRAIN',
          'primary_label': '恢复压力',
          'version_status': 'CURRENT',
          'version_status_label': '当前有效',
        },
      },
    ],
    'next_cursor': null,
  };

  @override
  Future<Map<String, dynamic>> getPatterns() async => {
    'patterns': [
      {
        'description': '过去 4 次「晚睡」之后，3 次出现「睡眠偏低」。',
        'display_status': 'ESTABLISHED',
        'display_status_label': '较稳定规律',
        'first_seen_date': '2026-08-01',
        'last_seen_date': '2026-08-24',
        'counter_examples': 1,
      },
    ],
    'observing_count': 0,
  };
}

AppEnv envWith(Map<String, dynamic> today) {
  final env = AppEnv(baseUrl: 'http://localhost:0', token: 't');
  env.client = FakeClient(today);
  return env;
}

Map<String, dynamic> todayBase({
  String state = 'C',
  String label = '今天值得调整',
  List actions = const ['今天优先补充睡眠', '暂缓高强度训练', '避免咖啡因在下午之后摄入'],
  List order = const ['conclusion', 'cause', 'action'],
  bool medical = false,
}) {
  return {
    'schema': 'l7.today/v1',
    'product_state': state,
    'product_state_label': label,
    'headline': 'headline-$state',
    'information_order': order,
    'cause': {
      'hypothesis_type': 'SLEEP_DEFICIT',
      'text': '最近可能因为睡眠不足。',
      'secondary': null,
    },
    'actions': actions,
    'confidence': 'LOW',
    'confidence_label': '较低',
    'medical_attention': medical,
    'analysis_date': '2026-08-16',
    'data_as_of': '2026-08-16',
    'updated_at_utc': '2026-08-17T13:00:00+00:00',
    'updated_at_local_hhmm': '21:00',
    'judgment_updated': false,
    'change_note': null,
    'evidence_level2': ['睡眠比你最近自己的通常水平低一些'],
    'feedback_prompt': null,
    'version_id': 1,
  };
}

void main() {
  test('TodayPayload parses contract fields', () {
    final p = TodayPayload(todayBase());
    expect(p.productState, 'C');
    expect(p.actions.length, 3);
    expect(p.updatedAtLocal, '21:00');
    expect(p.evidenceLevel2, isNotEmpty);
  });

  testWidgets('stable day renders zero actions (no forced filler advice)', (
    tester,
  ) async {
    SharedPreferences.setMockInitialValues({});
    final env = envWith(
      todayBase(state: 'A', label: '整体稳定', actions: const []),
    );
    await tester.pumpWidget(MaterialApp(home: TodayScreen(env: env)));
    await tester.pumpAndSettle();

    expect(find.text('整体稳定'), findsOneWidget);
    expect(find.text('今日行动'), findsNothing);
  });

  testWidgets('notable change shows conclusion, cause and ≤3 actions', (
    tester,
  ) async {
    SharedPreferences.setMockInitialValues({});
    final env = envWith(todayBase());
    await tester.pumpWidget(MaterialApp(home: TodayScreen(env: env)));
    await tester.pumpAndSettle();

    expect(find.text('今天值得调整'), findsOneWidget);
    expect(find.text('最可能原因'), findsOneWidget);
    expect(find.text('今日行动'), findsOneWidget);
    expect(find.text('今天优先补充睡眠'), findsOneWidget);
    expect(find.text('更新于 21:00'), findsOneWidget);
    expect(find.text('查看依据'), findsOneWidget);
    expect(find.text('问问我的状态'), findsOneWidget);
    expect(find.text('补充我的情况'), findsOneWidget);
  });

  testWidgets('Today and version history display labels, never machine enums', (
    tester,
  ) async {
    SharedPreferences.setMockInitialValues({});
    final payload = todayBase();
    payload['judgment_updated'] = true;
    payload['cause'] = {
      ...(payload['cause'] as Map),
      'secondary': {
        'hypothesis_type': 'RECOVERY_STRAIN',
        'hypothesis_label': '恢复压力',
      },
    };
    final env = AppEnv(baseUrl: 'http://localhost:0', token: 't');
    env.client = ProductConformanceClient(payload);

    await tester.pumpWidget(MaterialApp(home: TodayScreen(env: env)));
    await tester.pumpAndSettle();
    expect(find.textContaining('次要可能：恢复压力'), findsOneWidget);
    expect(find.textContaining('RECOVERY_STRAIN'), findsNothing);

    await tester.tap(find.text('查看变化来源'));
    await tester.pumpAndSettle();
    expect(find.textContaining('健康安全关注'), findsOneWidget);
    expect(find.textContaining('打开应用'), findsOneWidget);
    expect(find.textContaining('app_open'), findsNothing);
  });

  testWidgets(
    'Evidence displays exact interpreted values and no status enums',
    (tester) async {
      final env = AppEnv(baseUrl: 'http://localhost:0', token: 't');
      env.client = ProductConformanceClient(todayBase());
      await tester.pumpWidget(MaterialApp(home: EvidenceScreen(env: env)));
      await tester.pumpAndSettle();

      expect(find.text('REM 睡眠占比'), findsOneWidget);
      expect(find.textContaining('当前 12.0%'), findsOneWidget);
      expect(find.textContaining('个人近期基线 20.0%'), findsOneWidget);
      expect(find.textContaining('2 天前的数据'), findsOneWidget);
      expect(find.textContaining('PROVISIONAL'), findsNothing);
      expect(find.textContaining('BELOW_TYPICAL_RANGE'), findsNothing);
    },
  );

  testWidgets('Context displays canonical context and body labels', (
    tester,
  ) async {
    final env = AppEnv(baseUrl: 'http://localhost:0', token: 't');
    env.client = ProductConformanceClient(todayBase());
    await tester.pumpWidget(MaterialApp(home: ContextScreen(env: env)));
    await tester.pumpAndSettle();

    expect(find.text('高强度训练 · 腿部'), findsOneWidget);
    expect(find.textContaining('HIGH_INTENSITY_TRAINING'), findsNothing);
  });

  testWidgets('History timeline and Patterns use backend display labels', (
    tester,
  ) async {
    final env = AppEnv(baseUrl: 'http://localhost:0', token: 't');
    env.client = ProductConformanceClient(todayBase());
    await tester.pumpWidget(MaterialApp(home: HistoryScreen(env: env)));
    await tester.pumpAndSettle();
    await tester.tap(find.textContaining('恢复压力：'));
    await tester.pumpAndSettle();
    expect(find.textContaining('健康判断'), findsOneWidget);
    expect(find.textContaining('变化较明显'), findsOneWidget);
    expect(find.textContaining('NOTABLE_CHANGE'), findsNothing);

    await tester.pumpWidget(
      MaterialApp(
        key: UniqueKey(),
        home: PatternsScreen(env: env),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.textContaining('状态：较稳定规律'), findsOneWidget);
    expect(find.textContaining('ESTABLISHED'), findsNothing);
  });

  testWidgets('medical safety puts action before cause', (tester) async {
    SharedPreferences.setMockInitialValues({});
    final env = envWith(
      todayBase(
        state: 'E',
        label: '健康安全关注',
        medical: true,
        order: const ['conclusion', 'action', 'cause'],
        actions: const ['今天以休息为主', '如症状加重考虑就医评估'],
      ),
    );
    await tester.pumpWidget(MaterialApp(home: TodayScreen(env: env)));
    await tester.pumpAndSettle();

    final actionPos = tester.getTopLeft(find.text('今日行动'));
    final causePos = tester.getTopLeft(find.text('最可能原因'));
    expect(
      actionPos.dy < causePos.dy,
      isTrue,
      reason: 'For state E, actions must appear above the cause',
    );
  });

  testWidgets('no score-like widgets exist', (tester) async {
    SharedPreferences.setMockInitialValues({});
    final env = envWith(todayBase());
    await tester.pumpWidget(MaterialApp(home: TodayScreen(env: env)));
    await tester.pumpAndSettle();

    final textWidgets = find
        .byType(Text)
        .evaluate()
        .map((e) => e.widget as Text);
    for (final t in textWidgets) {
      final s = t.data ?? '';
      expect(
        s.contains('分') && RegExp(r'\d{1,3}\s*分').hasMatch(s),
        isFalse,
        reason: 'no numeric health score text allowed',
      );
      expect(
        RegExp(r'(readiness|score)', caseSensitive: false).hasMatch(s),
        isFalse,
      );
    }
  });

  testWidgets('cached Today remains visible when refresh fails', (
    tester,
  ) async {
    SharedPreferences.setMockInitialValues({});
    const baseUrl = 'https://example.invalid';
    final prefs = await SharedPreferences.getInstance();
    await ReadCache(
      prefs,
      serverId: baseUrl,
    ).store('today', todayBase(), version: 1);
    final env = AppEnv(baseUrl: baseUrl, token: 't', preferences: prefs);
    env.client = FailingClient(todayBase());

    await tester.pumpWidget(MaterialApp(home: TodayScreen(env: env)));
    await tester.pumpAndSettle();

    expect(find.text('今天值得调整'), findsOneWidget);
    expect(find.text('认证失败，请更新访问令牌'), findsOneWidget);
    expect(find.text('重新尝试'), findsOneWidget);
  });

  for (final entry in <String, Widget Function(AppEnv)>{
    'history': (env) => HistoryScreen(env: env),
    'patterns': (env) => PatternsScreen(env: env),
    'settings': (env) => MeScreen(env: env),
    'evidence': (env) => EvidenceScreen(env: env),
    'context': (env) => ContextScreen(env: env),
    'notifications': (env) => NotificationsScreen(env: env),
  }.entries) {
    testWidgets('${entry.key} exits loading with a retryable error', (
      tester,
    ) async {
      SharedPreferences.setMockInitialValues({});
      final env = AppEnv(baseUrl: 'https://example.invalid', token: 't');
      env.client = FailingClient(todayBase());

      await tester.pumpWidget(MaterialApp(home: entry.value(env)));
      await tester.pumpAndSettle();

      expect(find.text('认证失败，请更新访问令牌'), findsOneWidget);
      expect(find.text('重新尝试'), findsOneWidget);
    });
  }

  testWidgets('Q&A failure is sanitized and preserves a retry action', (
    tester,
  ) async {
    final env = AppEnv(baseUrl: 'https://example.invalid', token: 't');
    env.client = FailingClient(todayBase());

    await tester.pumpWidget(MaterialApp(home: QnAScreen(env: env)));
    await tester.enterText(find.byType(TextField), '今天适合训练吗？');
    await tester.tap(find.byIcon(Icons.send));
    await tester.pumpAndSettle();

    expect(find.text('认证失败，请更新访问令牌'), findsOneWidget);
    expect(find.text('重新尝试'), findsOneWidget);
    expect(find.textContaining('traceback'), findsNothing);
  });

  testWidgets('Q&A shows bounded staged processing status', (tester) async {
    final env = AppEnv(baseUrl: 'https://example.invalid', token: 't');
    final client = PendingQnAClient(todayBase());
    env.client = client;

    await tester.pumpWidget(MaterialApp(home: QnAScreen(env: env)));
    await tester.enterText(find.byType(TextField), '今天要散步吗？');
    await tester.tap(find.byIcon(Icons.send));
    await tester.pump();

    expect(find.text('正在理解你的问题'), findsOneWidget);

    await tester.pump(const Duration(seconds: 3));
    expect(find.text('正在结合你的健康数据'), findsOneWidget);

    await tester.pump(const Duration(seconds: 3));
    expect(find.text('正在进行安全检查（如需要）'), findsOneWidget);

    client.qaCompleter.complete({
      'conversation_id': 1,
      'direct_answer': '可以散步，但以轻松活动为主。',
      'actions': <String>[],
      'scope': 'HEALTH_DECISION',
      'medical_review_state': 'PERFORMED',
      'evidence_ref': {'grounded': true},
    });
    await tester.pumpAndSettle();

    expect(find.text('可以散步，但以轻松活动为主。'), findsOneWidget);
    expect(find.textContaining('正在'), findsNothing);
  });
}
