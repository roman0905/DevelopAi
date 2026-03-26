-- 新增血糖查询插件到模型供应器表
DELETE FROM `ai_model_provider`
WHERE `model_type` = 'Plugin'
  AND `provider_code` = 'get_glucose_data';

INSERT INTO `ai_model_provider` (
    `id`, `model_type`, `provider_code`, `name`, `fields`, `sort`,
    `creator`, `create_date`, `updater`, `update_date`
) VALUES (
    'SYSTEM_PLUGIN_GLUCOSE',
    'Plugin',
    'get_glucose_data',
    '血糖数据查询',
    JSON_ARRAY(),
    80,
    0, NOW(), 0, NOW()
);
