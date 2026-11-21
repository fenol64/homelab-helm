{{/*
Expand the name of the chart.
*/}}
{{- define "homelab-immich.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "homelab-immich.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "homelab-immich.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "homelab-immich.labels" -}}
helm.sh/chart: {{ include "homelab-immich.chart" . }}
{{ include "homelab-immich.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "homelab-immich.selectorLabels" -}}
app.kubernetes.io/name: {{ include "homelab-immich.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "homelab-immich.namespace" -}}
{{- .Values.namespace.name | default .Release.Namespace }}
{{- end }}

{{- define "homelab-immich.componentLabels" -}}
{{ include "homelab-immich.labels" .root }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{- define "homelab-immich.componentSelectorLabels" -}}
{{ include "homelab-immich.selectorLabels" .root }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{- define "homelab-immich.secretName" -}}
{{- printf "%s-secret" (include "homelab-immich.fullname" .) }}
{{- end }}

{{- define "homelab-immich.libraryPvcName" -}}
{{- if .Values.storage.library.existingClaim }}
{{- .Values.storage.library.existingClaim }}
{{- else }}
{{- printf "%s-library" (include "homelab-immich.fullname" .) }}
{{- end }}
{{- end }}

{{- define "homelab-immich.databasePvcName" -}}
{{- printf "%s-database" (include "homelab-immich.fullname" .) }}
{{- end }}

{{- define "homelab-immich.modelCachePvcName" -}}
{{- printf "%s-model-cache" (include "homelab-immich.fullname" .) }}
{{- end }}

{{/*
Short DNS names. Immich defaults the ML URL to http://immich-machine-learning:3003
and that name is not configurable by env, so the Service keeps this fixed name.
*/}}
{{- define "homelab-immich.databaseHost" -}}
{{- printf "%s-database" (include "homelab-immich.fullname" .) }}
{{- end }}

{{- define "homelab-immich.valkeyHost" -}}
{{- printf "%s-valkey" (include "homelab-immich.fullname" .) }}
{{- end }}

{{- define "homelab-immich.machineLearningHost" -}}
immich-machine-learning
{{- end }}
