/// E2E transport check: exercises the real HttpApiClient (same code path as the app)
/// against the live L7 Product API. No browser needed.
library;

import 'dart:io';

import 'package:phe_app/api_client.dart';

Future<void> main() async {
  final base = Platform.environment['L7_E2E_BASE'] ?? 'http://127.0.0.1:8707';
  final token = Platform.environment['L7_E2E_TOKEN'] ?? 'dev-local-token';
  final client = HttpApiClient(baseUrl: base, token: token);

  var failures = 0;
  void check(String name, bool cond, [String? extra]) {
    print('${cond ? "PASS" : "FAIL"}  $name${extra == null ? "" : "  ($extra)"}');
    if (!cond) failures++;
  }

  try {
    final health = await client.health();
    check('health', health['status'] == 'ok');

    final today = await client.getToday();
    check('today schema', today.schema == 'l7.today/v1');
    check('today state valid', ['A', 'B', 'C', 'D', 'E'].contains(today.productState));
    check('today actions <= 3', today.actions.length <= 3);
    check('today has cause', today.causeText.isNotEmpty);
    check('today updated_at_local', RegExp(r'^\d{2}:\d{2}$').hasMatch(today.updatedAtLocal));

    final versions = await client.getTodayVersions();
    check('versions non-empty', (versions['versions'] as List).isNotEmpty);

    final evidence = await client.getEvidence();
    check('evidence has metrics list', evidence['metrics'] is List);

    final patterns = await client.getPatterns();
    check('patterns shape', patterns.containsKey('observing_count'));

    final settings = await client.getSettings();
    check('settings shape', (settings['settings'] as Map).containsKey('notification_mode'));

    final usage = await client.getUsage();
    check('usage shape', usage.containsKey('total_model_calls'));

    // ---- Phase E transport ----
    final ctxList = await client.listContext();
    check('context list shape', ctxList['contexts'] is List);

    final pending = await client.getToday(); // keep-alive; pending-question below
    check('today still ok', pending.schema == 'l7.today/v1');

    final conv = await client.qaOpenConversation();
    check('qa conversation opens', conv['conversation_id'] is int);

    // ---- Phase F transport ----
    final episodes = await client.getEpisodes();
    check('episodes shape', episodes['episodes'] is List);
    final search = await client.searchHistory('睡眠');
    check('history search shape', search['results'] is List);

    // ---- Phase G transport ----
    final notifs = await client.getNotifications();
    check('notifications shape', notifs['notifications'] is List);
    final decisions = await client.getNotificationDecisions();
    check('decision audit shape', decisions['decisions'] is List);
  } catch (e) {
    print('FAIL  transport: $e');
    failures++;
  }

  print(failures == 0 ? 'E2E: PASS' : 'E2E: FAIL ($failures)');
  exit(failures == 0 ? 0 : 1);
}
