import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:phe_app/read_cache.dart';

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  test('cache envelope preserves schema server version and etag', () async {
    final prefs = await SharedPreferences.getInstance();
    final cache = ReadCache(prefs, serverId: 'prod');
    await cache.store('today', {'version_id': 4}, version: 4, etag: '"e4"');

    final entry = await cache.read('today');
    expect(entry!.schema, ReadCache.schema);
    expect(entry.serverId, 'prod');
    expect(entry.version, 4);
    expect(entry.etag, '"e4"');
    expect(entry.data['version_id'], 4);
  });

  test('corruption is isolated to one key', () async {
    final prefs = await SharedPreferences.getInstance();
    final cache = ReadCache(prefs, serverId: 'prod');
    await prefs.setString(cache.storageKey('today'), '{broken');
    await cache.store('history', {'episodes': []}, version: 1);

    expect(await cache.read('today'), isNull);
    expect(await cache.read('history'), isNotNull);
    expect(prefs.containsKey(cache.storageKey('today')), isFalse);
  });

  test('older responses cannot overwrite newer cached versions', () async {
    final prefs = await SharedPreferences.getInstance();
    final cache = ReadCache(prefs, serverId: 'prod');
    await cache.store('today', {'version_id': 5}, version: 5);
    await cache.store('today', {'version_id': 4}, version: 4);
    expect((await cache.read('today'))!.data['version_id'], 5);
  });

  test('explicit invalidation removes only the selected resource', () async {
    final prefs = await SharedPreferences.getInstance();
    final cache = ReadCache(prefs, serverId: 'prod');
    await cache.store('today', {'version_id': 1}, version: 1);
    await cache.store('patterns', {'patterns': []}, version: 1);
    await cache.invalidate('today');
    expect(await cache.read('today'), isNull);
    expect(await cache.read('patterns'), isNotNull);
  });

  test('oversized payload is not persisted', () async {
    final prefs = await SharedPreferences.getInstance();
    final cache = ReadCache(prefs, serverId: 'prod', maxBytes: 100);
    await cache.store('context', {
      'text': List.filled(500, 'x').join(),
    }, version: 1);
    expect(await cache.read('context'), isNull);
  });
}
