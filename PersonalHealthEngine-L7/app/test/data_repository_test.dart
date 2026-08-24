import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:phe_app/api_client.dart';
import 'package:phe_app/data_repository.dart';
import 'package:phe_app/read_cache.dart';

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  test('same resource shares one in-flight request', () async {
    final prefs = await SharedPreferences.getInstance();
    final repository = DataRepository(ReadCache(prefs, serverId: 'prod'));
    final completer = Completer<ApiReadResponse>();
    var calls = 0;

    Future<ApiReadResponse> fetch(String? etag) {
      calls += 1;
      return completer.future;
    }

    final first = repository.refresh(
      'today',
      fetch: fetch,
      versionOf: (data) => data['version_id'] as int,
    );
    final second = repository.refresh(
      'today',
      fetch: fetch,
      versionOf: (data) => data['version_id'] as int,
    );
    await Future<void>.delayed(Duration.zero);
    expect(calls, 1);
    completer.complete(ApiReadResponse(data: {'version_id': 2}, etag: '"v2"'));
    expect((await first)!['version_id'], 2);
    expect((await second)!['version_id'], 2);
  });

  test('304 returns cached data and preserves etag', () async {
    final prefs = await SharedPreferences.getInstance();
    final cache = ReadCache(prefs, serverId: 'prod');
    await cache.store('today', {'version_id': 3}, version: 3, etag: '"v3"');
    final repository = DataRepository(cache);
    String? sentEtag;

    final result = await repository.refresh(
      'today',
      fetch: (etag) async {
        sentEtag = etag;
        return const ApiReadResponse.notModified();
      },
      versionOf: (data) => data['version_id'] as int,
    );
    expect(sentEtag, '"v3"');
    expect(result!['version_id'], 3);
    expect((await cache.read('today'))!.etag, '"v3"');
  });

  test('failed refresh leaves stale cache available', () async {
    final prefs = await SharedPreferences.getInstance();
    final cache = ReadCache(prefs, serverId: 'prod');
    await cache.store('patterns', {
      'patterns': ['cached'],
    }, version: 1);
    final repository = DataRepository(cache);

    await expectLater(
      repository.refresh(
        'patterns',
        fetch: (_) => throw const ApiException(ApiErrorKind.timeout, 'timeout'),
        versionOf: (_) => 2,
      ),
      throwsA(isA<ApiException>()),
    );
    expect((await repository.cached('patterns'))!['patterns'], ['cached']);
  });
}
