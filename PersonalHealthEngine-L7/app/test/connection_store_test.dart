import 'package:flutter_test/flutter_test.dart';
import 'package:phe_app/connection_store.dart';
import 'package:shared_preferences/shared_preferences.dart';

class FakeSecretStore implements SecretStore {
  final Map<String, String> values;
  FakeSecretStore([Map<String, String>? initial]) : values = {...?initial};

  @override
  Future<void> delete(String key) async => values.remove(key);

  @override
  Future<String?> read(String key) async => values[key];

  @override
  Future<void> write(String key, String value) async => values[key] = value;
}

void main() {
  test('production endpoint is HTTPS and requires no user configuration', () {
    expect(ConnectionStore.productionBaseUrl, 'https://47.111.229.39');
  });

  test('build token initializes secure storage on first launch', () async {
    SharedPreferences.setMockInitialValues({});
    final preferences = await SharedPreferences.getInstance();
    final secrets = FakeSecretStore();
    final store = ConnectionStore(
      preferences: preferences,
      secrets: secrets,
      initialToken: 'release-token',
    );

    final settings = await store.load();

    expect(settings.baseUrl, ConnectionStore.productionBaseUrl);
    expect(settings.token, 'release-token');
    expect(secrets.values[ConnectionStore.tokenKey], 'release-token');
    expect(preferences.containsKey(ConnectionStore.legacyTokenKey), isFalse);
  });

  test(
    'secure token wins over the token embedded for initial install',
    () async {
      SharedPreferences.setMockInitialValues({});
      final preferences = await SharedPreferences.getInstance();
      final secrets = FakeSecretStore({
        ConnectionStore.tokenKey: 'rotated-token',
      });
      final store = ConnectionStore(
        preferences: preferences,
        secrets: secrets,
        initialToken: 'release-token',
      );

      final settings = await store.load();

      expect(settings.token, 'rotated-token');
    },
  );

  test('legacy SharedPreferences token is migrated then deleted', () async {
    SharedPreferences.setMockInitialValues({
      ConnectionStore.legacyTokenKey: 'legacy-token',
    });
    final preferences = await SharedPreferences.getInstance();
    final secrets = FakeSecretStore();
    final store = ConnectionStore(
      preferences: preferences,
      secrets: secrets,
      initialToken: 'release-token',
    );

    final settings = await store.load();

    expect(settings.token, 'legacy-token');
    expect(secrets.values[ConnectionStore.tokenKey], 'legacy-token');
    expect(preferences.containsKey(ConnectionStore.legacyTokenKey), isFalse);
  });

  test('connection updates persist URL and token in separate stores', () async {
    SharedPreferences.setMockInitialValues({});
    final preferences = await SharedPreferences.getInstance();
    final secrets = FakeSecretStore();
    final store = ConnectionStore(
      preferences: preferences,
      secrets: secrets,
      initialToken: 'release-token',
    );

    await store.save('https://example.test/', 'new-token');

    expect(
      preferences.getString(ConnectionStore.baseUrlKey),
      'https://example.test',
    );
    expect(secrets.values[ConnectionStore.tokenKey], 'new-token');
  });
}
