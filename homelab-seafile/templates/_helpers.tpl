{{- define "homelab-seafile.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "homelab-seafile.fullname" -}}
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

{{- define "homelab-seafile.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "homelab-seafile.labels" -}}
helm.sh/chart: {{ include "homelab-seafile.chart" . }}
{{ include "homelab-seafile.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "homelab-seafile.selectorLabels" -}}
app.kubernetes.io/name: {{ include "homelab-seafile.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "homelab-seafile.namespace" -}}
{{- .Values.namespace.name | default .Release.Namespace }}
{{- end }}

{{- define "homelab-seafile.componentLabels" -}}
{{ include "homelab-seafile.labels" .root }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{- define "homelab-seafile.componentSelectorLabels" -}}
{{ include "homelab-seafile.selectorLabels" .root }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{- define "homelab-seafile.secretName" -}}
{{- printf "%s-secret" (include "homelab-seafile.fullname" .) }}
{{- end }}

{{- define "homelab-seafile.dataPvcName" -}}
{{- if .Values.storage.data.existingClaim }}
{{- .Values.storage.data.existingClaim }}
{{- else }}
{{- printf "%s-data" (include "homelab-seafile.fullname" .) }}
{{- end }}
{{- end }}

{{- define "homelab-seafile.databasePvcName" -}}
{{- printf "%s-database" (include "homelab-seafile.fullname" .) }}
{{- end }}

{{- define "homelab-seafile.databaseHost" -}}
{{- printf "%s-database" (include "homelab-seafile.fullname" .) }}
{{- end }}

{{- define "homelab-seafile.redisHost" -}}
{{- printf "%s-redis" (include "homelab-seafile.fullname" .) }}
{{- end }}

{{- define "homelab-seafile.publicHost" -}}
{{- .Values.server.publicHost | default .Values.server.ingress.host }}
{{- end }}

{{- define "homelab-seafile.publicOrigin" -}}
{{- printf "%s://%s" .Values.server.protocol (include "homelab-seafile.publicHost" .) }}
{{- end }}
