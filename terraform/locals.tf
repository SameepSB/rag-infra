
locals {
  rg_name        = var.resource_group_name
  location       = var.location
  prefix         = var.name_prefix

  kv_name        = "${local.prefix}-kv"
  st_name        = lower(replace("${local.prefix}st", "-", "")) # storage must be globally unique & only lowercase
  aoai_name      = "${local.prefix}-aoai"
  search_name    = "${local.prefix}-search"
  acr_name       = "${local.prefix}acr"
  insights_name  = "${local.prefix}-ai"
  la_name        = "${local.prefix}-law"
  aca_env_name   = "${local.prefix}-aca-env"
}

