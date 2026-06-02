terraform {
  required_providers {
    openstack = {
      source  = "terraform-provider-openstack/openstack"
      version = ">= 1.52.1"
    }
    azurerm = {
      source  = "hashicorp/azurerm"
      version = ">= 4.0.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.6.0"
    }
  }
}

provider "openstack" {}

provider "azurerm" {
  features {}
}

provider "random" {}
