import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:phe_app/api_client.dart';

void main() {
  HttpApiClient clientReturning(int status, String body) {
    return HttpApiClient(
      baseUrl: 'https://47.111.229.39',
      token: 'test-token',
      client: MockClient((request) async {
        expect(request.headers['Authorization'], 'Bearer test-token');
        return http.Response(body, status);
      }),
    );
  }

  test(
    'authentication failures are stable and do not expose response bodies',
    () async {
      final client = clientReturning(401, 'SENSITIVE SERVER TRACEBACK');

      await expectLater(
        client.getToday(),
        throwsA(
          isA<ApiException>()
              .having((e) => e.kind, 'kind', ApiErrorKind.authentication)
              .having((e) => e.userMessage, 'message', '认证失败，请更新访问令牌')
              .having(
                (e) => e.toString(),
                'sanitized',
                isNot(contains('SENSITIVE')),
              ),
        ),
      );
    },
  );

  test(
    'server failures are stable and do not expose response bodies',
    () async {
      final client = clientReturning(503, 'python traceback: private path');

      await expectLater(
        client.getToday(),
        throwsA(
          isA<ApiException>()
              .having((e) => e.kind, 'kind', ApiErrorKind.server)
              .having((e) => e.userMessage, 'message', '服务器暂时不可用，请稍后重试')
              .having(
                (e) => e.toString(),
                'sanitized',
                isNot(contains('traceback')),
              ),
        ),
      );
    },
  );

  test('invalid JSON becomes an explicit invalid-response error', () async {
    final client = clientReturning(200, '<html>not json</html>');

    await expectLater(
      client.getToday(),
      throwsA(
        isA<ApiException>()
            .having((e) => e.kind, 'kind', ApiErrorKind.invalidResponse)
            .having((e) => e.userMessage, 'message', '服务器返回的数据无效'),
      ),
    );
  });

  test('client connection failures become cannot-connect errors', () async {
    final client = HttpApiClient(
      baseUrl: 'https://47.111.229.39',
      token: 'test-token',
      client: MockClient(
        (_) => throw http.ClientException('connection refused'),
      ),
    );

    await expectLater(
      client.getToday(),
      throwsA(
        isA<ApiException>()
            .having((e) => e.kind, 'kind', ApiErrorKind.cannotConnect)
            .having((e) => e.userMessage, 'message', '无法连接服务器'),
      ),
    );
  });

  test('read timeout becomes an explicit timeout error', () async {
    final client = HttpApiClient(
      baseUrl: 'https://47.111.229.39',
      token: 'test-token',
      normalTimeout: const Duration(milliseconds: 5),
      client: MockClient((_) async {
        await Future<void>.delayed(const Duration(milliseconds: 50));
        return http.Response(jsonEncode({'status': 'ok'}), 200);
      }),
    );

    await expectLater(
      client.health(),
      throwsA(
        isA<ApiException>()
            .having((e) => e.kind, 'kind', ApiErrorKind.timeout)
            .having((e) => e.userMessage, 'message', '请求超时，请重新尝试'),
      ),
    );
  });

  test('deferred qna uses normal timeout and sends idempotency key', () async {
    final client = HttpApiClient(
      baseUrl: 'https://47.111.229.39',
      token: 'test-token',
      normalTimeout: const Duration(milliseconds: 100),
      inferenceTimeout: const Duration(milliseconds: 5),
      client: MockClient((request) async {
        expect(request.headers['Idempotency-Key'], 'qa-retry-1');
        await Future<void>.delayed(const Duration(milliseconds: 30));
        return http.Response(
          jsonEncode({'accepted': true, 'job_id': 9, 'status': 'PENDING'}),
          202,
        );
      }),
    );

    final result = await client.qaAsk(
      '今天能训练吗？',
      idempotencyKey: 'qa-retry-1',
    );
    expect(result['job_id'], 9);
  });

  test('conditional reads send ETag and accept 304 without decoding', () async {
    final client = HttpApiClient(
      baseUrl: 'https://47.111.229.39',
      token: 'test-token',
      client: MockClient((request) async {
        expect(request.headers['If-None-Match'], '"today-v1"');
        return http.Response('', 304);
      }),
    );
    final result = await client.conditionalGet('/today', etag: '"today-v1"');
    expect(result.notModified, isTrue);
  });

  test('async writes preserve idempotency key and accept 202', () async {
    var calls = 0;
    final client = HttpApiClient(
      baseUrl: 'https://47.111.229.39',
      token: 'test-token',
      client: MockClient((request) async {
        calls += 1;
        expect(request.headers['Idempotency-Key'], 'context-retry-1');
        return http.Response(
          jsonEncode({'accepted': true, 'job_id': 7, 'status': 'PENDING'}),
          202,
        );
      }),
    );
    final first = await client.addContext(
      '今天头疼', idempotencyKey: 'context-retry-1',
    );
    final retry = await client.addContext(
      '今天头疼', idempotencyKey: 'context-retry-1',
    );
    expect(first['job_id'], retry['job_id']);
    expect(calls, 2);
  });

  test('cursor reads request the bounded next page', () async {
    final client = HttpApiClient(
      baseUrl: 'https://47.111.229.39',
      token: 'test-token',
      client: MockClient((request) async {
        expect(request.url.queryParameters, {'limit': '20', 'cursor': '42'});
        return http.Response(jsonEncode({'episodes': [], 'next_cursor': null}), 200);
      }),
    );
    await client.getEpisodes(cursor: 42, limit: 20);
  });
}
