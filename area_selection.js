// Показываем начальный участок; выбор города изменит положение карты.
const map = L.map('map').setView([56.47, 84.985], 13);
// Используем обычные изображения OSM с обязательным указанием источника.
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {maxZoom: 19, attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'}).addTo(map);
// Храним только одну выбранную территорию.
const area = L.featureGroup().addTo(map);
// Получаем элементы сообщений и экспорта.
const status = document.getElementById('status');
// Блокируем сохранение до корректного выбора территории.
const exportButton = document.getElementById('export');
// Сохраняем рамку поиска отдельно от точного контура.
let bbox = null;
// Прямоугольный режим совместим с прежним конфигом без geometry.
let geometry = null;
// Переносим происхождение выбранной границы в настройки.
let areaSource = null;
// Отменяем устаревшие результаты загрузки при новом выборе.
let selectionVersion = 0;
// Храним названия городов из локального файла.
let cities = [];
// Читаем ключи выбранных категорий.
const selectedCategories = () => [...document.querySelectorAll('#categories input:checked')].map(input => input.value);
// Удаляем пустые строки свободного поиска.
const selectedQueries = () => document.getElementById('queries').value.split('\n').map(value => value.trim()).filter(Boolean);
// Проверяем наличие территории и хотя бы одного поискового задания.
const update = () => { exportButton.disabled = !bbox || (!selectedCategories().length && !selectedQueries().length); };
// Объясняем начало прямоугольного выделения.
L.drawLocal.draw.handlers.rectangle.tooltip.start = 'Нажмите и протяните прямоугольник.';
// Объясняем завершение прямоугольного выделения.
L.drawLocal.draw.handlers.simpleshape.tooltip.end = 'Отпустите кнопку мыши для завершения.';
// Переводим начало рисования полигона.
L.drawLocal.draw.handlers.polygon.tooltip.start = 'Нажмите на карту, чтобы отметить первую вершину.';
// Объясняем добавление следующих вершин.
L.drawLocal.draw.handlers.polygon.tooltip.cont = 'Отметьте следующую вершину.';
// Объясняем замыкание контура.
L.drawLocal.draw.handlers.polygon.tooltip.end = 'Нажмите первую вершину, чтобы завершить полигон.';
// Создаём один обработчик для каждого способа рисования.
const rectangleDrawer = new L.Draw.Rectangle(map);
// Не разрешаем рисовать самопересекающийся контур.
const polygonDrawer = new L.Draw.Polygon(map, {allowIntersection: false, drawError: {message: 'Стороны полигона не должны пересекаться.'}});
// Отключаем оба инструмента перед переключением способа выбора.
const stopDrawing = () => { rectangleDrawer.disable(); polygonDrawer.disable(); };
// Включаем прямоугольное выделение без параллельных обработчиков.
document.getElementById('draw').addEventListener('click', () => { selectionVersion++; stopDrawing(); rectangleDrawer.enable(); });
// Включаем последовательное рисование вершин.
document.getElementById('draw-polygon').addEventListener('click', () => { selectionVersion++; stopDrawing(); polygonDrawer.enable(); });

// Проверяем структуру загружаемого GeoJSON до изменения текущего выбора.
function checkGeoJSON(value, budget = {vertices: 0}) {
  // Требуем объект с явно указанным типом.
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Граница должна быть объектом GeoJSON.');
  // Не угадываем проекцию чужого файла.
  if (value.crs != null) throw new Error('Преобразуйте файл в WGS84 и удалите поле crs.');
  // Проверяем коллекцию без игнорирования неполигональных объектов.
  if (value.type === 'FeatureCollection') {
    // Пустая коллекция не является территорией.
    if (!Array.isArray(value.features) || !value.features.length) throw new Error('В коллекции нет полигонов.');
    // Проверяем каждую часть территории теми же правилами.
    value.features.forEach(feature => checkGeoJSON(feature, budget));
    // Возвращаем исходные координаты без упрощения.
    return value;
  }
  // Свойства объекта не участвуют в отборе организаций.
  if (value.type === 'Feature') return checkGeoJSON(value.geometry, budget);
  // Поддерживаем только площади.
  if (!['Polygon', 'MultiPolygon'].includes(value.type)) throw new Error('Допустимы только Polygon и MultiPolygon.');
  // Приводим контуры к одному виду для предварительной проверки.
  const polygons = value.type === 'Polygon' ? [value.coordinates] : value.coordinates;
  // Проверяем наличие хотя бы одного полигона.
  if (!Array.isArray(polygons) || !polygons.length) throw new Error('В границе нет координат.');
  // Проверяем внешние контуры и отверстия.
  for (const polygon of polygons) {
    // Требуем внешний контур каждой части.
    if (!Array.isArray(polygon) || !polygon.length) throw new Error('В полигоне отсутствует контур.');
    // Проверяем каждое кольцо отдельно.
    for (const ring of polygon) {
      // Нельзя молча замыкать ошибочный файл.
      if (!Array.isArray(ring) || ring.length < 4 || JSON.stringify(ring[0]) !== JSON.stringify(ring.at(-1))) throw new Error('Контур должен быть замкнут и иметь не менее четырёх координат.');
      // Считаем вершины всех объектов импортируемой границы без сокращения точности.
      budget.vertices += ring.length;
      // Большой файл нужно предварительно разделить, а не упрощать автоматически.
      if (budget.vertices > 200000) throw new Error('Допускается до 200 000 вершин во всей границе.');
      // Проверяем двумерный порядок долгота, широта.
      for (const point of ring) {
        // Исключаем строки, бесконечность и координаты в метрах.
        if (!Array.isArray(point) || point.length !== 2 || !point.every(Number.isFinite) || Math.abs(point[0]) > 180 || Math.abs(point[1]) > 90) throw new Error('Нужны числовые координаты WGS84: долгота, широта.');
      }
      // Проверяем отсутствие перехода через 180-й меридиан.
      if (ring.slice(1).some((point, i) => Math.abs(point[0] - ring[i][0]) > 180)) throw new Error('Переход через 180-й меридиан не поддерживается.');
    }
  }
  // Полную проверку самопересечений и отверстий выполнит сборщик перед открытием браузера.
  return value;
}

// Заменяем территорию только после успешного чтения и предварительной проверки.
function selectGeometry(value, source, fit = true) {
  // Проверяем координаты до создания слоя.
  checkGeoJSON(value);
  // Создаём новый слой, сохраняя отдельные контуры и отверстия.
  const layer = L.geoJSON(value);
  // Получаем фактическую рамку территории.
  const bounds = layer.getBounds();
  // Не принимаем вырожденную рамку за область.
  if (!bounds.isValid() || bounds.getWest() === bounds.getEast() || bounds.getSouth() === bounds.getNorth()) throw new Error('Граница не имеет площади.');
  // Отключаем рисование после выбора файла или города.
  stopDrawing();
  // Оставляем только новую успешно прочитанную геометрию.
  area.clearLayers();
  // Показываем контур на карте.
  area.addLayer(layer);
  // Прямоугольник экспортируется прежним способом, полигон — с точным контуром.
  geometry = source.kind === 'rectangle' ? null : value;
  // Сохраняем координаты рамки без округления.
  bbox = [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()];
  // Сохраняем происхождение границы.
  areaSource = source;
  // Округляем только текстовое отображение.
  document.getElementById('bounds').value = bbox.map(number => number.toFixed(6)).join(', ');
  // Показываем дату снимка отдельно от даты загрузки.
  document.getElementById('area-info').textContent = source.label;
  // При выборе города или файла приближаем карту к его контуру.
  if (fit) map.fitBounds(bounds, {padding: [20, 20]});
  // Обозначаем дальнейшую полную проверку в сборщике.
  status.textContent = 'Территория выбрана. Перед сбором программа проверит корректность контуров.';
  // Разрешаем экспорт при наличии поискового задания.
  update();
}

// Преобразуем нарисованную область в GeoJSON без ручной перестановки координат.
map.on('draw:created', event => {
  // Новый выбор отменяет предыдущую незавершённую загрузку.
  selectionVersion++;
  // Ошибка рисования не должна уничтожить ранее выбранную территорию.
  try {
    // Сохраняем полигон либо совместимый прямоугольник.
    selectGeometry(event.layer.toGeoJSON().geometry, {kind: event.layerType, label: event.layerType === 'rectangle' ? 'Прямоугольник, выбранный на карте.' : 'Полигон, нарисованный на карте.'}, false);
  // Показываем причину ошибки прямо в интерфейсе.
  } catch (error) { status.textContent = error.message; }
});
// Читаем GeoJSON только с локального диска пользователя.
document.getElementById('geojson-file').addEventListener('change', async event => {
  // Получаем явно выбранный файл.
  const file = event.target.files[0];
  // Отмена диалога не меняет текущий выбор.
  if (!file) return;
  // Запоминаем версию выбора для асинхронного чтения.
  const version = ++selectionVersion;
  // Ошибка файла сохраняет прежнюю территорию.
  try {
    // Проверяем размер до чтения содержимого.
    if (file.size > 10 * 1024 * 1024) throw new Error('Файл больше 10 МБ; разделите территорию.');
    // Разбираем текст без загрузки его на внешний сервер.
    const value = JSON.parse(await file.text());
    // Не заменяем более новый выбор запоздавшим чтением.
    if (version !== selectionVersion) return;
    // Показываем полный импортированный контур.
    selectGeometry(value, {kind: 'file', label: 'Файл: ' + file.name});
  // Объясняем ошибку JSON или координат.
  } catch (error) { if (version === selectionVersion) status.textContent = 'Не удалось загрузить границу: ' + error.message; }
  // Позволяем повторно выбрать тот же файл после его исправления.
  event.target.value = '';
});
// Нормализуем русские и латинские варианты названий.
const normalize = value => value.toLowerCase().replaceAll('ё', 'е').trim();
// Фильтруем только локальный список городов.
function showCities() {
  // Читаем введённое название.
  const query = normalize(document.getElementById('city-search').value);
  // Получаем контейнер результатов.
  const container = document.getElementById('city-results');
  // Удаляем устаревшие варианты.
  container.replaceChildren();
  // Не выбираем город автоматически при пустом запросе.
  if (!query) return;
  // Находим совпадения по названию и сохранённым вариантам написания.
  const matches = cities.filter(city => [city.name, ...(city.aliases || [])].some(name => normalize(name).includes(query)));
  // Объясняем ограниченность встроенного каталога.
  if (!matches.length) container.textContent = 'В локальном каталоге нет такого города. Загрузите GeoJSON или нарисуйте полигон.';
  // Показываем отдельное действие для каждого найденного города.
  for (const city of matches) {
    // Создаём безопасную текстовую кнопку.
    const button = document.createElement('button');
    // Не запускаем отправку формы.
    button.type = 'button';
    // Показываем название и дату снимка до выбора.
    button.textContent = city.label;
    // Загружаем готовый файл из той же папки локального сервера.
    button.addEventListener('click', async () => {
      // Запоминаем порядок выбора для защиты от запоздавших ответов.
      const version = ++selectionVersion;
      // Сетевые ошибки локального сервера не должны уничтожать текущую область.
      try {
        // Разрешаем только файлы внутри каталога границ.
        if (!/^boundaries\/[a-z0-9-]+\.geojson$/.test(city.file)) throw new Error('Некорректный путь в каталоге границ.');
        // Читаем локальный статический GeoJSON без геокодирующего сервиса.
        const response = await fetch(city.file);
        // Отличаем отсутствие файла от неверной геометрии.
        if (!response.ok) throw new Error('Файл границы отсутствует.');
        // Разбираем исходный контур.
        const value = await response.json();
        // Более новый выбор имеет приоритет.
        if (version !== selectionVersion) return;
        // Сохраняем название, происхождение и архивную дату в конфиге.
        selectGeometry(value, {kind: 'catalog', label: city.label, snapshot: city.snapshot, source_url: city.source_url});
      // Показываем понятную причину неудачной загрузки.
      } catch (error) { if (version === selectionVersion) status.textContent = error.message; }
    });
    // Добавляем вариант без исполнения HTML из каталога.
    container.append(button);
  }
}
// Обновляем локальные результаты при вводе названия.
document.getElementById('city-search').addEventListener('input', showCities);
// Читаем индекс каталога из локального статического файла.
fetch('boundaries/catalog.json').then(response => {
  // Проверяем наличие каталога.
  if (!response.ok) throw new Error('Не удалось прочитать каталог границ.');
  // Разбираем названия городов.
  return response.json();
// Обрабатываем содержимое успешно прочитанного локального справочника.
}).then(catalog => {
  // Проверяем структуру индекса.
  if (!Array.isArray(catalog.cities)) throw new Error('В каталоге отсутствует список cities.');
  // Храним только метаданные, без загрузки всех контуров сразу.
  cities = catalog.cities;
  // Учитываем название, введённое до завершения загрузки.
  showCities();
// Ошибка каталога не блокирует рисование или импорт файла.
}).catch(error => { document.getElementById('city-results').textContent = error.message; });
// Следим за выбором категорий.
document.getElementById('categories').addEventListener('change', update);
// Следим за изменением свободных запросов.
document.getElementById('queries').addEventListener('input', update);
// Экспортируем настройки только после явного нажатия.
exportButton.addEventListener('click', () => {
  // Сохраняем рамку и поисковые задания в прежнем формате.
  const config = {bbox, categories: selectedCategories(), queries: selectedQueries(), area_source: areaSource};
  // Добавляем точный контур только для полигонального режима.
  if (geometry) config.geometry = geometry;
  // Формируем файл UTF-8 с полными координатами.
  const blob = new Blob([JSON.stringify(config, null, 2)], {type: 'application/json;charset=utf-8'});
  // Создаём временную ссылку на локальный файл.
  const url = URL.createObjectURL(blob);
  // Подготавливаем сохранение средствами браузера.
  const link = document.createElement('a');
  // Назначаем созданную ссылку.
  link.href = url;
  // Сохраняем совместимое имя конфига.
  link.download = 'area.json';
  // Запускаем загрузку файла.
  link.click();
  // Освобождаем память после начала загрузки.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  // Объясняем следующий шаг.
  status.textContent = 'area.json сохранён. Поместите его в папку репозитория и выполните команду.';
});
// Загружаем единый локальный список поисковых категорий.
fetch('categories.json').then(response => {
  // Проверяем наличие файла.
  if (!response.ok) throw new Error('Не удалось прочитать categories.json');
  // Разбираем содержимое JSON.
  return response.json();
// Обрабатываем содержимое успешно прочитанного локального справочника.
}).then(catalog => {
  // Получаем контейнер категорий.
  const container = document.getElementById('categories');
  // Удаляем временное сообщение загрузки.
  container.textContent = '';
  // Собираем большой список вне страницы и вставляем его одним действием.
  const fragment = document.createDocumentFragment();
  // Создаём выбор для каждой предустановки.
  for (const [key, item] of Object.entries(catalog.categories)) {
    // Объединяем флажок и текст в доступную подпись.
    const label = document.createElement('label');
    // Применяем оформление строки.
    label.className = 'category';
    // Подготавливаем строку поиска один раз, включая тематическую группу.
    label.dataset.search = (item.label + ' ' + (item.group || '')).toLocaleLowerCase('ru').replaceAll('ё', 'е');
    // Показываем группу при наведении на рубрику.
    label.title = item.group || 'Готовая поисковая настройка';
    // Создаём флажок выбора.
    const input = document.createElement('input');
    // Разрешаем выбрать несколько категорий.
    input.type = 'checkbox';
    // Сохраняем ключ Python-справочника.
    input.value = key;
    // По умолчанию выбираем кафе.
    input.checked = key === 'cafe';
    // Используем текст без исполнения HTML из файла.
    label.append(input, document.createTextNode(item.label));
    // Показываем готовую категорию.
    fragment.append(label);
  }
  // Вставляем все категории без последовательной перерисовки каждой строки.
  container.append(fragment);
  // Используем одно поле поиска без изменения состояния выбранных флажков.
  const filter = document.getElementById('category-filter');
  // Фильтруем уже созданные строки по названию и группе.
  const filterCategories = () => {
    // Игнорируем регистр и различие букв е и ё.
    const words = filter.value.trim().toLocaleLowerCase('ru').replaceAll('ё', 'е').split(/\s+/).filter(Boolean);
    // Список не пересоздаётся, поэтому выбранные категории сохраняются.
    for (const label of container.children) {
      // Явно отключаем отображение строки независимо от правила display для класса.
      label.style.display = words.every(word => label.dataset.search.includes(word)) ? '' : 'none';
    }
  };
  // Обновляем список после каждого изменения поисковой строки.
  filter.addEventListener('input', filterCategories);
  // Применяем также текст, введённый до завершения загрузки справочника.
  filterCategories();
  // Проверяем доступность экспорта.
  update();
// Объясняем необходимость локального статического сервера.
}).catch(error => { status.textContent = error.message + '. Откройте страницу через локальный сервер по инструкции README.'; });
