ui = true
disable_mlock = true

api_addr = "https://hashibank-vault:8200"
cluster_addr = "https://hashibank-vault:8201"

listener "tcp" {
  address = "0.0.0.0:8200"
  cluster_address = "0.0.0.0:8201"
  tls_disable = false
  tls_cert_file = "/vault/config/tls/hashibank-vault.crt"
  tls_key_file = "/vault/config/tls/hashibank-vault.key"
  tls_disable_client_certs = false
  tls_require_and_verify_client_cert = false
}

storage "raft" {
  path = "/vault/raft"
  node_id = "hashibank-vault"
}
