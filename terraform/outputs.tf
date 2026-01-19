
output "resource_group" {
  value = azurerm_resource_group.rg.name
}

output "key_vault_uri" {
  value = azurerm_key_vault.kv.vault_uri
}

output "storage_account_name" {
  value = azurerm_storage_account.st.name
}

output "acr_login_server" {
  value = azurerm_container_registry.acr.login_server
}

output "aoai_endpoint" {
  value = azurerm_cognitive_account.aoai.endpoint
}

output "search_service_endpoint" {
  value = "https://${azurerm_search_service.search.name}.search.windows.net"
}

output "app_insights_connection_string" {
  value = azurerm_application_insights.ai.connection_string
}

output "aca_environment_id" {
  value = azurerm_container_app_environment.aca_env.id
}

