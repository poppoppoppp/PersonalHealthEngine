/// Versioned bounded first-page cache for stale-while-revalidate reads.
library;

import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

class CacheEntry {
  final String schema;
  final String serverId;
  final int version;
  final String? etag;
  final DateTime storedAt;
  final Map<String, dynamic> data;

  const CacheEntry({
    required this.schema,
    required this.serverId,
    required this.version,
    required this.etag,
    required this.storedAt,
    required this.data,
  });
}

class ReadCache {
  static const schema = 'phe.read-cache/v1';

  final SharedPreferences preferences;
  final String serverId;
  final int maxBytes;

  const ReadCache(
    this.preferences, {
    required this.serverId,
    this.maxBytes = 128 * 1024,
  });

  String storageKey(String resource) {
    final safeServer = base64Url
        .encode(utf8.encode(serverId))
        .replaceAll('=', '');
    return 'phe.read.v1.$safeServer.$resource';
  }

  Future<CacheEntry?> read(String resource) async {
    final key = storageKey(resource);
    final raw = preferences.getString(key);
    if (raw == null) return null;
    try {
      final decoded = jsonDecode(raw);
      if (decoded is! Map ||
          decoded['schema'] != schema ||
          decoded['server_id'] != serverId ||
          decoded['version'] is! int ||
          decoded['data'] is! Map ||
          decoded['stored_at_utc'] is! String) {
        throw const FormatException('invalid cache envelope');
      }
      return CacheEntry(
        schema: schema,
        serverId: serverId,
        version: decoded['version'] as int,
        etag: decoded['etag'] as String?,
        storedAt: DateTime.parse(decoded['stored_at_utc'] as String),
        data: (decoded['data'] as Map).cast<String, dynamic>(),
      );
    } catch (_) {
      await preferences.remove(key);
      return null;
    }
  }

  Future<void> store(
    String resource,
    Map<String, dynamic> data, {
    required int version,
    String? etag,
  }) async {
    final current = await read(resource);
    if (current != null && current.version > version) return;
    final encoded = jsonEncode({
      'schema': schema,
      'server_id': serverId,
      'version': version,
      'etag': etag,
      'stored_at_utc': DateTime.now().toUtc().toIso8601String(),
      'data': data,
    });
    if (utf8.encode(encoded).length > maxBytes) return;
    await preferences.setString(storageKey(resource), encoded);
  }

  Future<void> invalidate(String resource) =>
      preferences.remove(storageKey(resource));
}
