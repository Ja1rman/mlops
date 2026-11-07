{{- define "mlops-search.name" -}}
{{- printf "%s-mlops-search" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "mlops-search.labels" -}}
app.kubernetes.io/name: mlops-search
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

