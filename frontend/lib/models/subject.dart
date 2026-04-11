/// Mirrors `adhd_engine.api.subjects.SubjectOut` on the server.
class Subject {
  final String id;
  final String displayName;
  final DateTime? birthDate;
  final String? sex;
  final String notes;

  const Subject({
    required this.id,
    required this.displayName,
    this.birthDate,
    this.sex,
    this.notes = '',
  });

  factory Subject.fromJson(Map<String, dynamic> json) {
    return Subject(
      id: json['id'] as String,
      displayName: json['display_name'] as String,
      birthDate: json['birth_date'] == null
          ? null
          : DateTime.tryParse(json['birth_date'] as String),
      sex: json['sex'] as String?,
      notes: (json['notes'] as String?) ?? '',
    );
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'display_name': displayName,
        if (birthDate != null) 'birth_date': birthDate!.toIso8601String().substring(0, 10),
        if (sex != null) 'sex': sex,
        'notes': notes,
      };
}
