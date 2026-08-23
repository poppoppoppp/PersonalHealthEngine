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

  test('inference calls use the separate long timeout', () async {
    final client = HttpApiClient(
      baseUrl: 'https://47.111.229.39',
      token: 'test-token',
      normalTimeout: const Duration(milliseconds: 5),
      inferenceTimeout: const Duration(milliseconds: 100),
      client: MockClient((_) async {
        await Future<void>.delayed(const Duration(milliseconds: 30));
        return http.Response(
          jsonEncode({'conversation_id': 1, 'direct_answer': 'ok'}),
          200,
        );
      }),
    );

    final result = await client.qaAsk('今天能训练吗？');
    expect(result['direct_answer'], 'ok');
  });
}
