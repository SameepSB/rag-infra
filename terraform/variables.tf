
variable "subscription_id" {
  type        = string
  description = "Azure subscription ID"
}

variable "resource_group_name" {
  type        = string
  description = "Resource group name"
  default     = "rg-agentic-rag"
}

variable "location" {
  type        = string
  description = "Azure region (e.g., eastus, westus2)"
  default     = "eastus"
}

variable "name_prefix" {
  type        = string
  description = "Short prefix for resource names"
  default     = "rag"
}

variable "enable_private_endpoints" {
  type        = bool
  description = "Set to true to create VNet + private endpoints for AOAI and AI Search"
  default     = false
}
# All Variables
