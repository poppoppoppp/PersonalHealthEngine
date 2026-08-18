/// L7 Product API client.
///
/// The client is a thin transport: every judgment, threshold and rendering decision is
/// made by the backend. The app never computes health state and never holds secrets other
/// than the user's own API token.
library;

import 'dart:convert';

import 'package:http/http.dart' as http;

class ApiException implements Exception {
  final int? status;
  final String message;
  ApiException(this.message, {this.status});
  @override
  String toString() => status == null ? message : 'HTTP $status: $message';
}

/// Parsed Current Today State (l7.today/v1).
class TodayPayload {
  final Map<String, dynamic> raw;
  TodayPayload(this.raw);

  String get schema => raw['schema'] as String? ?? '';
  String get productState => raw['product_state'] as String? ?? 'D';
  String get productStateLabel => raw['product_state_label'] as String? ?? '';
  String get headline => raw['headline'] as String? ?? '';
  List<String> get informationOrder =>
      (raw['information_order'] as List? ?? const []).map((e) => '$e').toList();
  Map<String, dynamic> get cause =>
      (raw['cause'] as Map?)?.cast<String, dynamic>() ?? const {};
  String get causeText => cause['text'] as String? ?? '';
  String? get causeType => cause['hypothesis_type'] as String?;
  Map<String, dynamic>? get secondaryCause =>
      (cause['secondary'] as Map?)?.cast<String, dynamic>();
  List<String> get actions =>
      (raw['actions'] as List? ?? const []).map((e) => '$e').toList();
  String get confidence => raw['confidence'] as String? ?? '';
  String get confidenceLabel => raw['confidence_label'] as String? ?? '';
  bool get medicalAttention => raw['medical_attention'] as bool? ?? false;
  String? get analysisDate => raw['analysis_date'] as String?;
  String? get dataAsOf => raw['data_as_of'] as String?;
  String? get updatedAtUtc => raw['updated_at_utc'] as String?;
  String get updatedAtLocal => raw['updated_at_local_hhmm'] as String? ?? '';
  bool get judgmentUpdated => raw['judgment_updated'] as bool? ?? false;
  String? get changeNote => raw['change_note'] as String?;
  List<String> get evidenceLevel2 =>
      (raw['evidence_level2'] as List? ?? const []).map((e) => '$e').toList();
  Map<String, dynamic>? get feedbackPrompt =>
      (raw['feedback_prompt'] as Map?)?.cast<String, dynamic>();
  int? get versionId => raw['version_id'] as int?;
}

abstract class L7Client {
  Future<TodayPayload> getToday();
  Future<Map<String, dynamic>> refreshToday();
  Future<Map<String, dynamic>> getTodayVersions();
  Future<Map<String, dynamic>> getEvidence();
  Future<Map<String, dynamic>> getPatterns();
  Future<Map<String, dynamic>> getSettings();
  Future<Map<String, dynamic>> putSettings(Map<String, dynamic> values);
  Future<Map<String, dynamic>> getUsage();
  Future<Map<String, dynamic>> health();
  // Phase E
  Future<Map<String, dynamic>> qaOpenConversation();
  Future<Map<String, dynamic>> qaConversation(int id);
  Future<Map<String, dynamic>> qaAsk(String question, {int? conversationId});
  Future<Map<String, dynamic>> listContext();
  Future<Map<String, dynamic>> addContext(String text);
  Future<Map<String, dynamic>> correctContext(int id, String text);
  Future<void> deleteContext(int id);
  Future<Map<String, dynamic>> submitFeedback(String verdict, {String? text});
  // Phase F
  Future<Map<String, dynamic>> getEpisodes();
  Future<Map<String, dynamic>> getEpisode(int id);
  Future<Map<String, dynamic>> searchHistory(String q);
  // Phase G
  Future<Map<String, dynamic>> getNotifications();
  Future<Map<String, dynamic>> getNotificationDecisions();
}

class HttpApiClient implements L7Client {
  String baseUrl;
  String token;
  final http.Client _client = http.Client();

  HttpApiClient({required this.baseUrl, required this.token});

  Uri _u(String path) => Uri.parse('$baseUrl$path');
  Map<String, String> get _headers => {
        'Authorization': 'Bearer $token',
        'Content-Type': 'application/json',
      };

  Future<Map<String, dynamic>> _get(String path) async {
    final res = await _client.get(_u(path), headers: _headers).timeout(
          const Duration(seconds: 60),
        );
    if (res.statusCode != 200) {
      throw ApiException(res.body, status: res.statusCode);
    }
    return (jsonDecode(utf8.decode(res.bodyBytes)) as Map).cast<String, dynamic>();
  }

  Future<Map<String, dynamic>> _send(
    String method,
    String path, [
    Map<String, dynamic>? body,
  ]) async {
    final req = http.Request(method, _u(path));
    req.headers.addAll(_headers);
    if (body != null) req.body = jsonEncode(body);
    final streamed = await _client.send(req).timeout(const Duration(seconds: 90));
    final res = await http.Response.fromStream(streamed);
    if (res.statusCode != 200) {
      throw ApiException(res.body, status: res.statusCode);
    }
    return (jsonDecode(utf8.decode(res.bodyBytes)) as Map).cast<String, dynamic>();
  }

  @override
  Future<TodayPayload> getToday() async =>
      TodayPayload(await _get('/today'));

  @override
  Future<Map<String, dynamic>> refreshToday() => _send('POST', '/today/refresh');

  @override
  Future<Map<String, dynamic>> getTodayVersions() => _get('/today/versions');

  @override
  Future<Map<String, dynamic>> getEvidence() => _get('/evidence/today');

  @override
  Future<Map<String, dynamic>> getPatterns() => _get('/patterns');

  @override
  Future<Map<String, dynamic>> getSettings() => _get('/settings');

  @override
  Future<Map<String, dynamic>> putSettings(Map<String, dynamic> values) =>
      _send('PUT', '/settings', values);

  @override
  Future<Map<String, dynamic>> getUsage() => _get('/usage');

  @override
  Future<Map<String, dynamic>> health() => _get('/health');

  // ---------------- Phase E ----------------
  @override
  Future<Map<String, dynamic>> qaOpenConversation() =>
      _send('POST', '/qa/conversations');

  @override
  Future<Map<String, dynamic>> qaConversation(int id) =>
      _get('/qa/conversations/$id');

  @override
  Future<Map<String, dynamic>> qaAsk(String question, {int? conversationId}) =>
      _send('POST', '/qa/ask', {
        'question': question,
        if (conversationId != null) 'conversation_id': conversationId,
      });

  @override
  Future<Map<String, dynamic>> listContext() => _get('/context');

  @override
  Future<Map<String, dynamic>> addContext(String text) =>
      _send('POST', '/context', {'text': text});

  @override
  Future<Map<String, dynamic>> correctContext(int id, String text) =>
      _send('PUT', '/context/$id', {'text': text});

  @override
  Future<void> deleteContext(int id) async {
    final req = http.Request('DELETE', _u('/context/$id'));
    req.headers.addAll(_headers);
    final streamed = await _client.send(req).timeout(const Duration(seconds: 90));
    final res = await http.Response.fromStream(streamed);
    if (res.statusCode != 200) {
      throw ApiException(res.body, status: res.statusCode);
    }
  }

  @override
  Future<Map<String, dynamic>> submitFeedback(String verdict, {String? text}) =>
      _send('POST', '/feedback', {
        'verdict': verdict,
        if (text != null && text.isNotEmpty) 'text': text,
      });

  // ---------------- Phase F ----------------
  @override
  Future<Map<String, dynamic>> getEpisodes() => _get('/history/episodes');

  @override
  Future<Map<String, dynamic>> getEpisode(int id) =>
      _get('/history/episodes/$id');

  @override
  Future<Map<String, dynamic>> searchHistory(String q) =>
      _get('/history/search?q=${Uri.encodeComponent(q)}');

  // ---------------- Phase G ----------------
  @override
  Future<Map<String, dynamic>> getNotifications() => _get('/notifications');

  @override
  Future<Map<String, dynamic>> getNotificationDecisions() =>
      _get('/notifications/decisions');
}
