import 'package:flutter/material.dart';

import '../api_client.dart';

String apiErrorMessage(Object error) {
  if (error is ApiException) return error.userMessage;
  return '请求失败，请重新尝试';
}

class ApiErrorView extends StatelessWidget {
  final Object error;
  final VoidCallback onRetry;

  const ApiErrorView({super.key, required this.error, required this.onRetry});

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              apiErrorMessage(error),
              style: const TextStyle(fontWeight: FontWeight.w600),
            ),
            const SizedBox(height: 12),
            FilledButton.tonal(onPressed: onRetry, child: const Text('重新尝试')),
          ],
        ),
      ),
    );
  }
}
