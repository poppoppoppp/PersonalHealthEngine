import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:phe_app/api_client.dart';
import 'package:phe_app/widgets/api_error_view.dart';

void main() {
  testWidgets('shows sanitized category and invokes retry', (tester) async {
    var retries = 0;
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ApiErrorView(
            error: const ApiException(
              ApiErrorKind.authentication,
              '认证失败，请更新访问令牌',
            ),
            onRetry: () => retries++,
          ),
        ),
      ),
    );

    expect(find.text('认证失败，请更新访问令牌'), findsOneWidget);
    expect(find.text('重新尝试'), findsOneWidget);
    await tester.tap(find.text('重新尝试'));
    expect(retries, 1);
  });

  testWidgets('never renders an unknown exception detail', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ApiErrorView(
            error: Exception('python traceback and private path'),
            onRetry: () {},
          ),
        ),
      ),
    );

    expect(find.text('请求失败，请重新尝试'), findsOneWidget);
    expect(find.textContaining('traceback'), findsNothing);
  });
}
