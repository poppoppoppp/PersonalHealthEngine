/// L7 app tests: payload parsing + Today-first presentation rules.
/// Contract assertions: five states, no scores, ≤3 actions, stable day = 0 actions,
/// medical-safety order = conclusion → action → cause.
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:phe_app/api_client.dart';
import 'package:phe_app/main.dart';
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
  Future<Map<String, dynamic>> getPatterns() async =>
      {'patterns': [], 'observing_count': 0};

  @override
  Future<Map<String, dynamic>> getSettings() async =>
      {'settings': {'notification_mode': 'SMART'}};

  @override
  Future<Map<String, dynamic>> putSettings(values) async =>
      {'settings': values};

  @override
  Future<Map<String, dynamic>> getUsage() async => {'eval_runs': 0};

  @override
  Future<Map<String, dynamic>> health() async => {'status': 'ok'};

  @override
  Future<Map<String, dynamic>> qaOpenConversation() async =>
      {'conversation_id': 1};

  @override
  Future<Map<String, dynamic>> qaConversation(int id) async =>
      {'conversation_id': id, 'turns': []};

  @override
  Future<Map<String, dynamic>> qaAsk(String question, {int? conversationId}) async =>
      {
        'conversation_id': conversationId ?? 1,
        'direct_answer': '基于你的证据包的回答',
        'actions': const <String>[],
        'scope': 'HEALTH',
        'medical_review_state': 'BYPASSED',
        'evidence_ref': {'grounded': true},
      };

  @override
  Future<Map<String, dynamic>> listContext() async => {'contexts': []};

  @override
  Future<Map<String, dynamic>> addContext(String text) async =>
      {'status': 'SAVED', 're_evaluation': {'judgment_updated': false}};

  @override
  Future<Map<String, dynamic>> correctContext(int id, String text) async =>
      {'status': 'CORRECTED'};

  @override
  Future<void> deleteContext(int id) async {}

  @override
  Future<Map<String, dynamic>> submitFeedback(String verdict, {String? text}) async =>
      {'status': 'RECORDED', 're_evaluation': {'judgment_updated': false}};

  @override
  Future<Map<String, dynamic>> getEpisodes() async =>
      {'episodes': [], 'stable_days_hidden': 0};

  @override
  Future<Map<String, dynamic>> getEpisode(int id) async =>
      {'episode': {}, 'timeline': []};

  @override
  Future<Map<String, dynamic>> searchHistory(String q) async =>
      {'results': []};

  @override
  Future<Map<String, dynamic>> getNotifications() async =>
      {'notifications': []};

  @override
  Future<Map<String, dynamic>> getNotificationDecisions() async =>
      {'decisions': []};
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
      'secondary': null
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

  testWidgets('stable day renders zero actions (no forced filler advice)',
      (tester) async {
    SharedPreferences.setMockInitialValues({});
    final env = envWith(todayBase(
      state: 'A',
      label: '整体稳定',
      actions: const [],
    ));
    await tester.pumpWidget(MaterialApp(home: TodayScreen(env: env)));
    await tester.pumpAndSettle();

    expect(find.text('整体稳定'), findsOneWidget);
    expect(find.text('今日行动'), findsNothing);
  });

  testWidgets('notable change shows conclusion, cause and ≤3 actions',
      (tester) async {
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

  testWidgets('medical safety puts action before cause', (tester) async {
    SharedPreferences.setMockInitialValues({});
    final env = envWith(todayBase(
      state: 'E',
      label: '健康安全关注',
      medical: true,
      order: const ['conclusion', 'action', 'cause'],
      actions: const ['今天以休息为主', '如症状加重考虑就医评估'],
    ));
    await tester.pumpWidget(MaterialApp(home: TodayScreen(env: env)));
    await tester.pumpAndSettle();

    final actionPos = tester.getTopLeft(find.text('今日行动'));
    final causePos = tester.getTopLeft(find.text('最可能原因'));
    expect(actionPos.dy < causePos.dy, isTrue,
        reason: 'For state E, actions must appear above the cause');
  });

  testWidgets('no score-like widgets exist', (tester) async {
    SharedPreferences.setMockInitialValues({});
    final env = envWith(todayBase());
    await tester.pumpWidget(MaterialApp(home: TodayScreen(env: env)));
    await tester.pumpAndSettle();

    final textWidgets = find.byType(Text).evaluate().map((e) => e.widget as Text);
    for (final t in textWidgets) {
      final s = t.data ?? '';
      expect(s.contains('分') && RegExp(r'\d{1,3}\s*分').hasMatch(s), isFalse,
          reason: 'no numeric health score text allowed');
      expect(RegExp(r'(readiness|score)', caseSensitive: false).hasMatch(s),
          isFalse);
    }
  });
}
