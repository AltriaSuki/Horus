%% convert_pupil_data.m
% 将 Pupil_dataset.mat 中的 MATLAB table 转换为 Python 易读的格式
% 输出: Pupil_dataset_converted.mat (v7 格式，不含 table 对象)

load('Pupil_dataset.mat');

n = length(Pupil_data);
fprintf('共 %d 个 session\n', n);

% 保存基本信息
subjects = zeros(n, 1);
ages = zeros(n, 1);
groups = cell(n, 1);

for i = 1:n
    subjects(i) = Pupil_data(i).Subject;
    ages(i) = Pupil_data(i).Age;
    groups{i} = Pupil_data(i).Group;
end

% 保存 Task_epocs 数据（核心）
% 每个 session 有不同数量的 trials
all_trial = cell(n, 1);
all_load = cell(n, 1);
all_distractor = cell(n, 1);
all_corrresponse = cell(n, 1);
all_perform = cell(n, 1);
all_rtime = cell(n, 1);
all_pupil = cell(n, 1);  % cell of matrices, each trial has 8000 timepoints

for i = 1:n
    try
        te = Pupil_data(i).Task_epocs;
        all_trial{i} = te.Trial;
        all_load{i} = te.Load;
        all_distractor{i} = te.Distractor;
        if ismember('CorrResponse', te.Properties.VariableNames)
            all_corrresponse{i} = te.CorrResponse;
        end
        all_perform{i} = te.Perform;
        all_rtime{i} = te.Rtime;
        
        % Pupil 列：每个 trial 是一个 1x8000 时间序列
        n_trials = height(te);
        pupil_mat = nan(n_trials, 8000);
        for j = 1:n_trials
            p = te.Pupil{j};
            if ~isempty(p)
                pupil_mat(j, 1:length(p)) = p(:)';
            end
        end
        all_pupil{i} = pupil_mat;
        
        fprintf('Session %d: %d trials\n', i, n_trials);
    catch ME
        fprintf('Session %d 错误: %s\n', i, ME.message);
    end
end

% 保存 Task_data（含 Position 信息）
all_position_x = cell(n, 1);
all_position_y = cell(n, 1);

for i = 1:n
    try
        td = Pupil_data(i).Task_data;
        if ismember('Position', td.Properties.VariableNames)
            pos = td.Position;
            if iscell(pos)
                % 如果 Position 是 cell，合并为矩阵
                all_pos = cell2mat(pos);
                if size(all_pos, 2) >= 2
                    all_position_x{i} = all_pos(:, 1);
                    all_position_y{i} = all_pos(:, 2);
                end
            elseif isnumeric(pos)
                if size(pos, 2) >= 2
                    all_position_x{i} = pos(:, 1);
                    all_position_y{i} = pos(:, 2);
                end
            end
        end
    catch ME
        fprintf('Position Session %d 错误: %s\n', i, ME.message);
    end
end

% 保存 WISC 数据
wisc_fields = {};
wisc_data = [];

try
    w = Pupil_data(1).Wisc;
    if istable(w)
        wisc_fields = w.Properties.VariableNames;
        wisc_data = nan(n, length(wisc_fields));
        for i = 1:n
            try
                w = Pupil_data(i).Wisc;
                for j = 1:length(wisc_fields)
                    val = w.(wisc_fields{j});
                    if isnumeric(val)
                        wisc_data(i, j) = val;
                    elseif iscategorical(val)
                        wisc_data(i, j) = double(val);
                    end
                end
            catch
            end
        end
    end
catch ME
    fprintf('WISC 错误: %s\n', ME.message);
end

% 保存为 v7 格式（Python scipy.io 可直接读取）
save('Pupil_dataset_converted.mat', '-v7', ...
    'subjects', 'ages', 'groups', ...
    'all_trial', 'all_load', 'all_distractor', 'all_corrresponse', ...
    'all_perform', 'all_rtime', 'all_pupil', ...
    'all_position_x', 'all_position_y', ...
    'wisc_fields', 'wisc_data');

fprintf('转换完成！已保存到 Pupil_dataset_converted.mat\n');
