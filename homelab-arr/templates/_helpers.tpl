{{/*
Expand the name of the chart.
*/}}
{{- define "homelab-arr.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "homelab-arr.fullname" -}}
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

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "homelab-arr.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "homelab-arr.labels" -}}
helm.sh/chart: {{ include "homelab-arr.chart" . }}
{{ include "homelab-arr.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "homelab-arr.selectorLabels" -}}
app.kubernetes.io/name: {{ include "homelab-arr.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Namespace helper
*/}}
{{- define "homelab-arr.namespace" -}}
{{- .Values.namespace.name | default .Release.Namespace }}
{{- end }}

{{/*
Component labels — expects $root and $component
*/}}
{{- define "homelab-arr.componentLabels" -}}
{{ include "homelab-arr.labels" .root }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{- define "homelab-arr.componentSelectorLabels" -}}
{{ include "homelab-arr.selectorLabels" .root }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/*
Shared media PVC name
*/}}
{{- define "homelab-arr.mediaPvcName" -}}
{{- if .Values.storage.media.existingClaim }}
{{- .Values.storage.media.existingClaim }}
{{- else }}
{{- printf "%s-media" (include "homelab-arr.fullname" .) }}
{{- end }}
{{- end }}

{{/*
Shared downloads PVC name
*/}}
{{- define "homelab-arr.downloadsPvcName" -}}
{{- if .Values.storage.downloads.existingClaim }}
{{- .Values.storage.downloads.existingClaim }}
{{- else }}
{{- printf "%s-downloads" (include "homelab-arr.fullname" .) }}
{{- end }}
{{- end }}

{{/*
Config PVC name for a component
*/}}
{{- define "homelab-arr.configPvcName" -}}
{{- printf "%s-%s-config" (include "homelab-arr.fullname" .root) .component }}
{{- end }}

{{/*
LinuxServer common env
*/}}
{{- define "homelab-arr.linuxserverEnv" -}}
- name: PUID
  value: {{ .Values.puid | quote }}
- name: PGID
  value: {{ .Values.pgid | quote }}
- name: TZ
  value: {{ .Values.timezone | quote }}
{{- end }}

{{/*
Should create ingress for component (global or per-app)
*/}}
{{- define "homelab-arr.ingressEnabled" -}}
{{- $global := .Values.ingress.enabled | default false }}
{{- $local := .app.ingress.enabled | default false }}
{{- if or $global $local }}true{{ else }}false{{ end }}
{{- end }}
