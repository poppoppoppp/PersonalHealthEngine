/// Request-coalesced stale-while-revalidate repository.
library;

import 'api_client.dart';
import 'read_cache.dart';

class DataRepository {
  final ReadCache cache;
  final Map<String, Future<Map<String, dynamic>?>> _inflight = {};

  DataRepository(this.cache);

  Future<Map<String, dynamic>?> cached(String resource) async =>
      (await cache.read(resource))?.data;

  Future<Map<String, dynamic>?> refresh(
    String resource, {
    required Future<ApiReadResponse> Function(String? etag) fetch,
    required int Function(Map<String, dynamic> data) versionOf,
  }) {
    return _inflight.putIfAbsent(resource, () async {
      try {
        final before = await cache.read(resource);
        final response = await fetch(before?.etag);
        if (response.notModified) return before?.data;
        final data = response.data;
        if (data == null) return before?.data;
        await cache.store(
          resource,
          data,
          version: versionOf(data),
          etag: response.etag,
        );
        return (await cache.read(resource))?.data ?? data;
      } finally {
        _inflight.remove(resource);
      }
    });
  }

  Future<Map<String, dynamic>?> refreshUsing(
    String resource, {
    required L7Client client,
    required String path,
    required Future<Map<String, dynamic>> Function() fallback,
    required int Function(Map<String, dynamic> data) versionOf,
  }) {
    return refresh(
      resource,
      fetch: (etag) async {
        if (client is ConditionalL7Client) {
          return (client as ConditionalL7Client).conditionalGet(path, etag: etag);
        }
        return ApiReadResponse(data: await fallback());
      },
      versionOf: versionOf,
    );
  }
}
