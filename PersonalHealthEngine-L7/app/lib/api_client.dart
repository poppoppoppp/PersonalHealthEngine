/// L7 Product API client.
///
/// The client is a thin transport: every judgment, threshold and rendering decision is
/// made by the backend. The app never computes health state and never holds secrets other
/// than the user's own API token.
library;

import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

enum ApiErrorKind {
  noNetwork,
  cannotConnect,
  authentication,
  server,
  timeout,
  invalidResponse,
}

class ApiException implements Exception {
  final ApiErrorKind kind;
  final int? status;
  final String userMessage;

  const ApiException(this.kind, this.userMessage, {this.status});

  @override
  String toString() => userMessage;
}

class ApiReadResponse {
  final Map<String, dynamic>? data;
  final String? etag;
  final bool notModified;

  const ApiReadResponse({required this.data, this.etag}) : notModified = false;
  const ApiReadResponse.notModified()
    : data = null,
      etag = null,
      notModified = true;
}

abstract class ConditionalL7Client {
  Future<ApiReadResponse> conditionalGet(String path, {String? etag});
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
  Future<Map<String, dynamic>> qaConversation(int id, {int? cursor, int limit = 30});
  Future<Map<String, dynamic>> qaAsk(String question, {int? conversationId});
  Future<Map<String, dynamic>> listContext({int? cursor, int limit = 30});
  Future<Map<String, dynamic>> addContext(
    String text, {
    String? idempotencyKey,
  });
  Future<Map<String, dynamic>> correctContext(
    int id,
    String text, {
    String? idempotencyKey,
  });
  Future<Map<String, dynamic>> deleteContext(int id, {String? idempotencyKey});
  Future<Map<String, dynamic>> submitFeedback(
    String verdict, {
    String? text,
    String? idempotencyKey,
  });
  Future<Map<String, dynamic>> getJobStatus(int id);
  // Phase F
  Future<Map<String, dynamic>> getEpisodes({int? cursor, int limit = 30});
  Future<Map<String, dynamic>> getEpisode(int id, {int? cursor, int limit = 30});
  Future<Map<String, dynamic>> searchHistory(String q);
  // Phase G
  Future<Map<String, dynamic>> getNotifications();
  Future<Map<String, dynamic>> getNotificationDecisions();
}

class HttpApiClient implements L7Client, ConditionalL7Client {
  String baseUrl;
  String token;
  final http.Client _client;
  final Duration normalTimeout;
  final Duration inferenceTimeout;

  HttpApiClient({
    required this.baseUrl,
    required this.token,
    http.Client? client,
    this.normalTimeout = const Duration(seconds: 15),
    this.inferenceTimeout = const Duration(minutes: 12),
  }) : _client = client ?? http.Client();

  Uri _u(String path) => Uri.parse('$baseUrl$path');
  Map<String, String> get _headers => {
    'Authorization': 'Bearer $token',
    'Content-Type': 'application/json',
    'Accept-Encoding': 'gzip',
  };

  ApiException _statusError(int status) {
    if (status == 401 || status == 403) {
      return ApiException(
        ApiErrorKind.authentication,
        '认证失败，请更新访问令牌',
        status: status,
      );
    }
    return ApiException(ApiErrorKind.server, '服务器暂时不可用，请稍后重试', status: status);
  }

  Map<String, dynamic> _decode(http.Response response) {
    if (response.statusCode != 200 && response.statusCode != 202) {
      throw _statusError(response.statusCode);
    }
    final decoded = jsonDecode(utf8.decode(response.bodyBytes));
    if (decoded is! Map) {
      throw const FormatException('response is not a JSON object');
    }
    return decoded.cast<String, dynamic>();
  }

  Future<T> _guard<T>(Future<T> Function() request, Duration timeout) async {
    try {
      return await request().timeout(timeout);
    } on ApiException {
      rethrow;
    } on TimeoutException {
      throw const ApiException(ApiErrorKind.timeout, '请求超时，请重新尝试');
    } on FormatException {
      throw const ApiException(ApiErrorKind.invalidResponse, '服务器返回的数据无效');
    } on http.ClientException catch (error) {
      final text = error.message.toLowerCase();
      final noNetwork =
          text.contains('network is unreachable') ||
          text.contains('network unreachable') ||
          text.contains('no route to host') ||
          text.contains('failed host lookup');
      throw ApiException(
        noNetwork ? ApiErrorKind.noNetwork : ApiErrorKind.cannotConnect,
        noNetwork ? '当前没有可用网络' : '无法连接服务器',
      );
    } on Exception {
      throw const ApiException(ApiErrorKind.cannotConnect, '无法连接服务器');
    }
  }

  Future<Map<String, dynamic>> _get(String path) async {
    return _guard(
      () async => _decode(await _client.get(_u(path), headers: _headers)),
      normalTimeout,
    );
  }

  @override
  Future<ApiReadResponse> conditionalGet(String path, {String? etag}) {
    return _guard(() async {
      final headers = {..._headers};
      if (etag != null) headers['If-None-Match'] = etag;
      final response = await _client.get(_u(path), headers: headers);
      if (response.statusCode == 304) {
        return const ApiReadResponse.notModified();
      }
      final data = _decode(response);
      return ApiReadResponse(data: data, etag: response.headers['etag']);
    }, normalTimeout);
  }

  Future<Map<String, dynamic>> _send(
    String method,
    String path, [
    Map<String, dynamic>? body,
    Duration? timeout,
    Map<String, String>? extraHeaders,
  ]) async {
    return _guard(() async {
      final req = http.Request(method, _u(path));
      req.headers.addAll(_headers);
      if (extraHeaders != null) req.headers.addAll(extraHeaders);
      if (body != null) req.body = jsonEncode(body);
      final streamed = await _client.send(req);
      return _decode(await http.Response.fromStream(streamed));
    }, timeout ?? normalTimeout);
  }

  @override
  Future<TodayPayload> getToday() async => TodayPayload(await _get('/today'));

  @override
  Future<Map<String, dynamic>> refreshToday() =>
      _send('POST', '/today/refresh', null, inferenceTimeout);

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
  Future<Map<String, dynamic>> qaConversation(
    int id, {int? cursor, int limit = 30}
  ) => _get('/qa/conversations/$id?limit=$limit${cursor == null ? '' : '&cursor=$cursor'}');

  @override
  Future<Map<String, dynamic>> qaAsk(String question, {int? conversationId}) =>
      _send('POST', '/qa/ask', {
        'question': question,
        'conversation_id': ?conversationId,
      }, inferenceTimeout);

  @override
  Future<Map<String, dynamic>> listContext({int? cursor, int limit = 30}) =>
      _get('/context?limit=$limit${cursor == null ? '' : '&cursor=$cursor'}');

  @override
  Future<Map<String, dynamic>> addContext(
    String text, {
    String? idempotencyKey,
  }) => _send(
    'POST',
    '/context',
    {'text': text},
    normalTimeout,
    idempotencyKey == null ? null : {'Idempotency-Key': idempotencyKey},
  );

  @override
  Future<Map<String, dynamic>> correctContext(
    int id,
    String text, {
    String? idempotencyKey,
  }) => _send(
    'PUT',
    '/context/$id',
    {'text': text},
    normalTimeout,
    idempotencyKey == null ? null : {'Idempotency-Key': idempotencyKey},
  );

  @override
  Future<Map<String, dynamic>> deleteContext(
    int id, {
    String? idempotencyKey,
  }) => _send(
    'DELETE',
    '/context/$id',
    null,
    normalTimeout,
    idempotencyKey == null ? null : {'Idempotency-Key': idempotencyKey},
  );

  @override
  Future<Map<String, dynamic>> submitFeedback(
    String verdict, {
    String? text,
    String? idempotencyKey,
  }) => _send(
    'POST',
    '/feedback',
    {'verdict': verdict, if (text != null && text.isNotEmpty) 'text': text},
    normalTimeout,
    idempotencyKey == null ? null : {'Idempotency-Key': idempotencyKey},
  );

  @override
  Future<Map<String, dynamic>> getJobStatus(int id) => _get('/jobs/$id');

  // ---------------- Phase F ----------------
  @override
  Future<Map<String, dynamic>> getEpisodes({int? cursor, int limit = 30}) =>
      _get('/history/episodes?limit=$limit${cursor == null ? '' : '&cursor=$cursor'}');

  @override
  Future<Map<String, dynamic>> getEpisode(
    int id, {int? cursor, int limit = 30}
  ) => _get('/history/episodes/$id?limit=$limit${cursor == null ? '' : '&cursor=$cursor'}');

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
