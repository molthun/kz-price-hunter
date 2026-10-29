# Исторические файлы переезда P17-E — для текущего прода НЕ применять

Эти файлы готовились к переезду по шаблону: контейнеры `kzph-prod*` и `docker-compose.prod.yml`. Они
отрепетированы на поддельном NAS 29.10.2026 (docs/PROD_MOVE_P17E.md, раздел «Репетиция переезда»).

На деле владелец перенёс свой compose с NAS: контейнеры `kz-price-hunter` и `kz-price-watchtower`,
а сам контейнер на NAS заархивировал и удалил. Поэтому:

- `rollback_to_nas.sh` неприменим: он запускает контейнеры NAS, которых больше нет;
- `move_from_nas.sh`, `docker-compose.prod.yml` и `prod.env.example` описывают несостоявшийся вариант.

Текущие инструменты лежат уровнем выше: `backup_to_nas.sh`, `restore_prod.sh`, `verify_public.sh`.
Порядок восстановления — docs/PROD_MOVE_P17E.md, раздел «Восстановление».
