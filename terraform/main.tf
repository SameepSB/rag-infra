
# Resource Group
resource "azurerm_resource_group" "rg" {
  name     = local.rg_name
  location = local.location
}

# Optional networking (only if private endpoints are enabled)
resource "azurerm_virtual_network" "vnet" {
  count               = var.enable_private_endpoints ? 1 : 0
  name                = "${local.prefix}-vnet"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  address_space       = ["10.10.0.0/16"]
}

resource "azurerm_subnet" "snet_pe" {
  count                          = var.enable_private_endpoints ? 1 : 0
  name                           = "${local.prefix}-pe-snet"
  resource_group_name            = azurerm_resource_group.rg.name
  virtual_network_name           = azurerm_virtual_network.vnet[0].name
  address_prefixes               = ["10.10.1.0/24"]
  enforce_private_link_endpoint_network_policies = true
}

# Key Vault
resource "azurerm_key_vault" "kv" {
  name                        = local.kv_name
  location                    = azurerm_resource_group.rg.location
  resource_group_name         = azurerm_resource_group.rg.name
  tenant_id                   = data.azurerm_client_config.current.tenant_id
  sku_name                    = "standard"
  purge_protection_enabled    = true
  soft_delete_retention_days  = 90
  enable_rbac_authorization   = true
}

data "azurerm_client_config" "current" {}

# Storage
resource "azurerm_storage_account" "st" {
  name                     = local.st_name
  resource_group_name      = azurerm_resource_group.rg.name
  location                 = azurerm_resource_group.rg.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
  min_tls_version          = "TLS1_2"
}

# Azure OpenAI (Cognitive Services with kind = OpenAI)
resource "azurerm_cognitive_account" "aoai" {
  name                = local.aoai_name
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  kind                = "OpenAI"
  sku_name            = "S0"

  # For production, use network_acls with default_action = "Deny" + private endpoints
  network_acls {
    default_action = var.enable_private_endpoints ? "Deny" : "Allow"
    bypass         = ["AzureServices"]
  }

  lifecycle {
    prevent_destroy = false
  }
}

# Azure AI Search
resource "azurerm_search_service" "search" {
  name                = local.search_name
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  sku                 = "standard"
  partition_count     = 1
  replica_count       = 1

  # Private endpoint secured mode available via network rules + PE
  hosting_mode        = "default"
}

# Log Analytics + App Insights
resource "azurerm_log_analytics_workspace" "law" {
  name                = local.la_name
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  sku                 = "PerGB2018"
  retention_in_days   = 30
}

resource "azurerm_application_insights" "ai" {
  name                = local.insights_name
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  application_type    = "web"
  workspace_id        = azurerm_log_analytics_workspace.law.id
}

# ACR
resource "azurerm_container_registry" "acr" {
  name                = local.acr_name
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  sku                 = "Basic"
  admin_enabled       = false
}

# ACA Environment
resource "azurerm_container_app_environment" "aca_env" {
  name                 = local.aca_env_name
  location             = azurerm_resource_group.rg.location
  resource_group_name  = azurerm_resource_group.rg.name

  log_analytics_workspace_id = azurerm_log_analytics_workspace.law.id
}

# --- Optional Private Endpoints (AOAI + AI Search) ---
resource "azurerm_private_endpoint" "pe_aoai" {
  count               = var.enable_private_endpoints ? 1 : 0
  name                = "${local.prefix}-pe-aoai"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  subnet_id           = azurerm_subnet.snet_pe[0].id

  private_service_connection {
    name                           = "aoai-pe-conn"
    private_connection_resource_id = azurerm_cognitive_account.aoai.id
    subresource_names              = ["account"] # Cognitive Account endpoint
    is_manual_connection           = false
  }
}

resource "azurerm_private_endpoint" "pe_search" {
  count               = var.enable_private_endpoints ? 1 : 0
  name                = "${local.prefix}-pe-search"
  location            = azurerm_resource_group.rg.location
  resource_group_name = azurerm_resource_group.rg.name
  subnet_id           = azurerm_subnet.snet_pe[0].id

  private_service_connection {
    name                           = "search-pe-conn"
    private_connection_resource_id = azurerm_search_service.search.id
    subresource_names              = ["searchService"]
    is_manual_connection           = false
  }
}

# --- Secrets in Key Vault (placeholder wiring) ---
# In practice you will store search admin key & AOAI keys here.
# AOAI keys and Search admin keys are generated by Azure; fetch via data sources or manually add as secrets.

resource "azurerm_key_vault_secret" "kv_placeholder" {
  name         = "PLACEHOLDER"
  value        = "set-real-secrets-post-provision"
  key_vault_id = azurerm_key_vault.kv.id
}
# Main.tf
